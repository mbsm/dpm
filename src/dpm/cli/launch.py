"""Declarative launch system with dependency-based group orchestration."""

import sys
import time
from collections import deque
from typing import Any, Dict, List, Set, Tuple

import yaml

from dpm.cli.wait import wait_for_state


def parse_launch_file(path: str) -> Dict[str, Any]:
    """Parse a YAML launch file.

    Returns dict with keys: name, timeout, processes, groups.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Launch file must be a YAML dict, got {type(data).__name__}")

    groups_raw = data.get("groups", {})
    if not isinstance(groups_raw, dict):
        raise ValueError("'groups' must be a mapping of group names to dependency specs")

    groups = {}
    for name, spec in groups_raw.items():
        spec = spec or {}
        requires = spec.get("requires", [])
        after = spec.get("after", [])
        if isinstance(requires, str):
            requires = [requires]
        if isinstance(after, str):
            after = [after]
        groups[name] = {"requires": requires, "after": after}

    return {
        "name": data.get("name", path),
        "timeout": float(data.get("timeout", 30)),
        "processes": data.get("processes", []),
        "groups": groups,
    }


def _validate_graph(groups: Dict[str, Dict]) -> None:
    """Validate that all referenced groups exist and there are no cycles."""
    names = set(groups.keys())

    # Check all references point to defined groups
    for name, spec in groups.items():
        for dep in spec["requires"] + spec["after"]:
            if dep not in names:
                raise ValueError(
                    f"Group '{name}' references unknown group '{dep}'"
                )

    # Cycle detection via topological sort attempt
    in_degree = {n: 0 for n in names}
    adj: Dict[str, List[str]] = {n: [] for n in names}
    for name, spec in groups.items():
        for dep in spec["requires"] + spec["after"]:
            adj[dep].append(name)
            in_degree[name] += 1

    queue = deque(n for n, d in in_degree.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(names):
        raise ValueError("Dependency cycle detected in group definitions")


def resolve_waves(groups: Dict[str, Dict]) -> List[List[str]]:
    """Topological sort into waves for parallel execution.

    Returns a list of waves, where each wave is a list of group names
    that can be started in parallel.
    """
    _validate_graph(groups)

    all_deps = {}
    for name, spec in groups.items():
        all_deps[name] = set(spec["requires"] + spec["after"])

    resolved: Set[str] = set()
    waves: List[List[str]] = []
    remaining = set(groups.keys())

    while remaining:
        wave = [n for n in remaining if all_deps[n] <= resolved]
        if not wave:
            # Should not happen after cycle check, but safety net
            raise ValueError("Unable to resolve dependency order")
        waves.append(sorted(wave))
        resolved.update(wave)
        remaining -= set(wave)

    return waves


def _procs_in_group(client, group_name: str) -> List[Tuple[str, str]]:
    """Return list of (host, name) for all processes in a group."""
    return [
        (host, name)
        for (host, name), info in client.procs.items()
        if (getattr(info, "group", "") or "") == group_name
    ]


def _start_group(client, group_name: str) -> List[Tuple[str, str]]:
    """Start all processes in a group across all hosts. Returns the proc list."""
    procs = _procs_in_group(client, group_name)
    hosts = {host for host, _ in procs}
    for host in hosts:
        client.start_group(group_name, host)
    return procs


def _stop_group(client, group_name: str) -> List[Tuple[str, str]]:
    """Stop all processes in a group across all hosts. Returns the proc list."""
    procs = _procs_in_group(client, group_name)
    hosts = {host for host, _ in procs}
    for host in hosts:
        client.stop_group(group_name, host)
    return procs


def _wait_group_running(
    client, group_name: str, timeout: float
) -> Tuple[bool, List[str]]:
    """Wait until all processes in the group are running.

    Returns (success, list_of_failed_proc_labels).
    """
    procs = _procs_in_group(client, group_name)
    failed = []
    for host, name in procs:
        if not wait_for_state(client, name, host, target="R", timeout=timeout):
            failed.append(f"{name}@{host}")
    return len(failed) == 0, failed


def _wait_group_stopped(
    client, group_name: str, timeout: float
) -> Tuple[bool, List[str]]:
    """Wait until all processes in the group are stopped."""
    procs = _procs_in_group(client, group_name)
    failed = []
    for host, name in procs:
        if not wait_for_state(client, name, host, not_target="R", timeout=timeout):
            failed.append(f"{name}@{host}")
    return len(failed) == 0, failed


def _create_processes(client, processes: List[Dict[str, Any]]) -> int:
    """Create process definitions. Returns number of errors."""
    errors = 0
    for spec in processes:
        try:
            client.create_proc(
                spec["name"],
                spec["cmd"],
                spec.get("group", ""),
                spec["host"],
                bool(spec.get("auto_restart", False)),
                bool(spec.get("realtime", False)),
                work_dir=spec.get("work_dir", ""),
                cpuset=str(spec.get("cpuset", "")),
                cpu_limit=float(spec.get("cpu_limit", 0.0)),
                mem_limit=int(spec.get("mem_limit", 0)),
                isolated=bool(spec.get("isolated", False)),
            )
            print(f"  Created {spec['name']}@{spec['host']}")
        except Exception as e:
            print(f"  Error creating {spec.get('name', '?')}: {e}", file=sys.stderr)
            errors += 1
    return errors


def run_launch(client, path: str, reverse: bool = False) -> int:
    """Execute a launch file. Returns exit code (0=success)."""
    script = parse_launch_file(path)
    groups = script["groups"]
    timeout = script["timeout"]
    mode = "Shutdown" if reverse else "Launch"

    if not groups:
        print(f"No groups defined in {path}", file=sys.stderr)
        return 1

    try:
        waves = resolve_waves(groups)
    except ValueError as e:
        print(f"Invalid launch file: {e}", file=sys.stderr)
        return 1

    # On launch: create processes first (if any)
    if not reverse and script["processes"]:
        print(f"Creating {len(script['processes'])} processes...")
        errors = _create_processes(client, script["processes"])
        if errors:
            print(f"  {errors} error(s) during process creation", file=sys.stderr)
            return 1
        # Allow telemetry to propagate
        time.sleep(1)

    if reverse:
        waves = [w for w in reversed(waves)]

    total_waves = len(waves)
    print(f"{mode}: {script['name']} ({total_waves} waves, {len(groups)} groups)")

    for i, wave in enumerate(waves, 1):
        print(f"  Wave {i}/{total_waves}: {', '.join(wave)}")

        if reverse:
            # Stop all groups in this wave
            for group_name in wave:
                procs = _stop_group(client, group_name)
                count = len(procs)
                print(f"    Stopping {group_name} ({count} processes)")

            # Wait for all to stop
            for group_name in wave:
                ok, failed = _wait_group_stopped(client, group_name, timeout)
                if not ok:
                    print(
                        f"    TIMEOUT: {group_name} not fully stopped: {', '.join(failed)}",
                        file=sys.stderr,
                    )
                    # Continue stopping remaining groups on shutdown
        else:
            # Start all groups in this wave in parallel
            for group_name in wave:
                procs = _start_group(client, group_name)
                count = len(procs)
                print(f"    Starting {group_name} ({count} processes)")

            # Wait for all to reach Running
            for group_name in wave:
                ok, failed = _wait_group_running(client, group_name, timeout)
                if not ok:
                    print(
                        f"    FAILED: {group_name} not running: {', '.join(failed)}",
                        file=sys.stderr,
                    )
                    # Check if any group in this wave that failed is a hard
                    # requirement of a later group — if so, abort.
                    dependents = []
                    for later_wave in waves[i:]:
                        for g in later_wave:
                            if group_name in groups[g]["requires"]:
                                dependents.append(g)
                    if dependents:
                        print(
                            f"    Aborting: {', '.join(dependents)} require {group_name}",
                            file=sys.stderr,
                        )
                        return 1
                    # It's only an `after` dependency — continue with a warning
                    print(f"    Continuing (no hard dependents)")

    print(f"\n{mode} complete.")
    return 0
