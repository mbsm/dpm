"""Domain operations — shared by CLI and GUI.

Each operation takes an optional ``Progress`` sink so callers can render
updates in their own medium (terminal prints, Qt dialog updates, pytest
caplog, …).

The code here is the single source of truth for process lifecycle
operations that span multiple LCM round-trips (move, launch, create from
YAML spec). The CLI and GUI modules are expected to be thin adapters.
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import yaml

from dpm.cli.wait import wait_for_proc_present, wait_for_state


# ---------------------------------------------------------------------------
# Progress sink
# ---------------------------------------------------------------------------

class Progress:
    """Default no-op sink. Subclass to render to a specific medium."""

    def info(self, msg: str) -> None:  # progress line
        pass

    def warn(self, msg: str) -> None:  # non-fatal issue
        pass


class StdoutProgress(Progress):
    """CLI default: info to stdout, warn to stderr."""

    def info(self, msg: str) -> None:
        print(msg)

    def warn(self, msg: str) -> None:
        print(msg, file=sys.stderr)


class CallbackProgress(Progress):
    """Route both info/warn through a single callable ``fn(level, msg)``."""

    def __init__(self, fn: Callable[[str, str], None]) -> None:
        self._fn = fn

    def info(self, msg: str) -> None:
        self._fn("info", msg)

    def warn(self, msg: str) -> None:
        self._fn("warn", msg)


_NULL_PROGRESS = Progress()


def _p(progress: Optional[Progress]) -> Progress:
    return progress if progress is not None else _NULL_PROGRESS


# ---------------------------------------------------------------------------
# Single-proc operations
# ---------------------------------------------------------------------------

def create_from_spec(client, spec: Dict[str, Any]) -> str:
    """Create a process from a YAML-style spec dict. Returns the "name@host" label.

    Raises ValueError if required fields are missing or malformed.
    """
    from dpm.spec_io import _validate_spec

    _validate_spec(spec)
    name = spec["name"]
    host = spec["host"]
    client.create_proc(
        name,
        spec["exec_command"],
        spec.get("group", ""),
        host,
        bool(spec.get("auto_restart", False)),
        bool(spec.get("realtime", False)),
        rt_priority=int(spec.get("rt_priority", 0) or 0),
        work_dir=spec.get("work_dir", ""),
        cpuset=str(spec.get("cpuset", "")),
        cpu_limit=float(spec.get("cpu_limit", 0.0)),
        mem_limit=int(spec.get("mem_limit", 0)),
        isolated=bool(spec.get("isolated", False)),
    )
    return f"{name}@{host}"


def move_process(
    client,
    src_name: str,
    src_host: str,
    dst_name: str,
    dst_host: str,
    *,
    progress: Optional[Progress] = None,
) -> Tuple[bool, str]:
    """Move a process between hosts. Returns ``(ok, message)``.

    Sequence: stop-on-source (if running) → create-on-destination →
    start-on-destination (if was running) → delete-from-source. If
    destination-creation fails and the source was running, the source is
    restarted as a rollback.
    """
    p = _p(progress)
    from dpm.spec_io import extract_proc_spec

    src_key = (src_host, src_name)
    src_proc = client.procs.get(src_key)
    if src_proc is None:
        return False, f"Process '{src_name}@{src_host}' not found."

    if dst_host not in client.hosts:
        available = ", ".join(sorted(client.hosts.keys()))
        return False, (
            f"Destination host '{dst_host}' not responding. "
            f"Available: {available}"
        )

    if (dst_host, dst_name) in client.procs:
        return False, (
            f"Process '{dst_name}@{dst_host}' already exists. "
            "Delete it first or use a different name."
        )

    spec = extract_proc_spec(src_proc)
    was_running = getattr(src_proc, "state", "") == "R"
    label = f"{src_name}@{src_host} -> {dst_name}@{dst_host}"

    if was_running:
        p.info(f"Stopping {src_name}@{src_host}...")
        client.stop_proc(src_name, src_host)
        if not wait_for_state(client, src_name, src_host, not_target="R", timeout=5.0):
            return False, f"Failed to stop {src_name}@{src_host}. Move aborted."

    p.info(f"Creating {dst_name}@{dst_host}...")
    client.create_proc(
        dst_name, spec["exec_command"], spec["group"], dst_host,
        spec["auto_restart"], spec["realtime"],
        rt_priority=int(spec.get("rt_priority", 0) or 0),
        isolated=spec["isolated"], work_dir=spec["work_dir"],
        cpuset=spec["cpuset"], cpu_limit=spec["cpu_limit"],
        mem_limit=spec["mem_limit"],
    )
    wait_for_state(client, dst_name, dst_host, target="T", timeout=5.0)

    if (dst_host, dst_name) not in client.procs:
        p.warn(f"Failed to create on {dst_host}. Rolling back...")
        if was_running:
            client.start_proc(src_name, src_host)
        return False, f"Failed to create {dst_name} on {dst_host}."

    if was_running:
        p.info(f"Starting {dst_name}@{dst_host}...")
        client.start_proc(dst_name, dst_host)
        if not wait_for_state(client, dst_name, dst_host, target="R"):
            p.warn(
                f"Start on {dst_host} not confirmed, but definition was created."
            )

    p.info(f"Removing {src_name}@{src_host}...")
    client.del_proc(src_name, src_host)

    return True, f"Moved {label}"


# ---------------------------------------------------------------------------
# Launch plan (pure)
# ---------------------------------------------------------------------------

def parse_launch_file(path: str) -> Dict[str, Any]:
    """Parse a YAML launch file.

    Returns dict with keys: ``name``, ``timeout``, ``processes``, ``groups``.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"Launch file must be a YAML dict, got {type(data).__name__}"
        )

    groups_raw = data.get("groups", {})
    if not isinstance(groups_raw, dict):
        raise ValueError(
            "'groups' must be a mapping of group names to dependency specs"
        )

    groups: Dict[str, Dict[str, List[str]]] = {}
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


def _validate_group_refs(groups: Dict[str, Dict]) -> None:
    """Raise ValueError if any group references a dependency that doesn't exist."""
    names = set(groups.keys())
    for name, spec in groups.items():
        for dep in spec["requires"] + spec["after"]:
            if dep not in names:
                raise ValueError(
                    f"Group '{name}' references unknown group '{dep}'"
                )


def resolve_waves(groups: Dict[str, Dict]) -> List[List[str]]:
    """Topological sort into waves for parallel execution.

    Returns a list of waves; each wave is a list of group names that can
    be started in parallel. Cycles are detected by an inability to make
    progress (no remaining group has all dependencies resolved).
    """
    _validate_group_refs(groups)

    all_deps = {
        name: set(spec["requires"] + spec["after"])
        for name, spec in groups.items()
    }

    resolved: Set[str] = set()
    waves: List[List[str]] = []
    remaining = set(groups.keys())

    while remaining:
        wave = [n for n in remaining if all_deps[n] <= resolved]
        if not wave:
            raise ValueError(
                f"Dependency cycle detected in group definitions "
                f"(unresolved: {sorted(remaining)})"
            )
        waves.append(sorted(wave))
        resolved.update(wave)
        remaining -= set(wave)

    return waves


# ---------------------------------------------------------------------------
# Launch execution
# ---------------------------------------------------------------------------

def _procs_in_group(client, group_name: str) -> List[Tuple[str, str]]:
    return [
        (host, name)
        for (host, name), info in client.procs.items()
        if (getattr(info, "group", "") or "") == group_name
    ]


def _fan_out_group(
    client, group_name: str, action_fn: Callable[[str, str], None]
) -> List[Tuple[str, str]]:
    """Invoke ``action_fn(group, host)`` for every host with procs in *group_name*."""
    procs = _procs_in_group(client, group_name)
    hosts = {host for host, _ in procs}
    for host in hosts:
        action_fn(group_name, host)
    return procs


def _format_proc_failure(client, host: str, name: str) -> str:
    """Render '<name>@<host>' with state/errors pulled from latest telemetry."""
    info = client.procs.get((host, name))
    label = f"{name}@{host}"
    if info is None:
        return f"{label} (no telemetry)"
    state = getattr(info, "state", "") or "?"
    errors = (getattr(info, "errors", "") or "").strip()
    if errors:
        return f"{label} [state={state}: {errors}]"
    return f"{label} [state={state}]"


def _wait_group(
    client, group_name: str, timeout: float, *, running: bool
) -> Tuple[bool, List[str]]:
    procs = _procs_in_group(client, group_name)
    failed: List[str] = []
    for host, name in procs:
        if running:
            ok = wait_for_state(client, name, host, target="R", timeout=timeout)
        else:
            ok = wait_for_state(client, name, host, not_target="R", timeout=timeout)
        if not ok:
            failed.append(_format_proc_failure(client, host, name))
    return len(failed) == 0, failed


def _create_processes_from_script(
    client, processes: List[Dict[str, Any]], progress: Progress
) -> Tuple[int, List[Tuple[str, str]]]:
    """Send create_proc commands. Returns (error_count, [(host, name), ...] sent)."""
    errors = 0
    sent: List[Tuple[str, str]] = []
    for spec in processes:
        try:
            client.create_proc(
                spec["name"],
                spec["cmd"],
                spec.get("group", ""),
                spec["host"],
                bool(spec.get("auto_restart", False)),
                bool(spec.get("realtime", False)),
                rt_priority=int(spec.get("rt_priority", 0) or 0),
                work_dir=spec.get("work_dir", ""),
                cpuset=str(spec.get("cpuset", "")),
                cpu_limit=float(spec.get("cpu_limit", 0.0)),
                mem_limit=int(spec.get("mem_limit", 0)),
                isolated=bool(spec.get("isolated", False)),
            )
            progress.info(f"  Created {spec['name']}@{spec['host']}")
            sent.append((spec["host"], spec["name"]))
        except Exception as e:
            progress.warn(f"  Error creating {spec.get('name', '?')}: {e}")
            errors += 1
    return errors, sent


def run_launch(
    client,
    script: Dict[str, Any],
    *,
    reverse: bool = False,
    progress: Optional[Progress] = None,
) -> Tuple[bool, str]:
    """Execute a parsed launch script. Returns ``(ok, summary_or_error)``.

    When *reverse* is True, waves are executed in reverse order with
    group stops instead of starts (shutdown semantics).
    """
    p = _p(progress)
    groups = script["groups"]
    timeout = script["timeout"]
    mode = "Shutdown" if reverse else "Launch"

    if not groups:
        return False, "No groups defined."

    try:
        waves = resolve_waves(groups)
    except ValueError as e:
        return False, f"Invalid launch file: {e}"

    if not reverse and script.get("processes"):
        procs = script["processes"]
        p.info(f"Creating {len(procs)} processes...")
        errors, sent = _create_processes_from_script(client, procs, p)
        if errors:
            return False, f"{errors} process(es) failed to create."

        # Block until every created proc shows up in client telemetry.
        # The daemon publishes host_procs on procs_status_interval (default
        # 5 s); without this poll, the fan-out below races the broadcast
        # and silently starts zero processes per group.
        telemetry_timeout = max(timeout, 10.0)
        missing: List[str] = []
        for host, name in sent:
            if not wait_for_proc_present(
                client, name, host, timeout=telemetry_timeout
            ):
                missing.append(f"{name}@{host}")
        if missing:
            return False, (
                "Created processes did not appear in telemetry within "
                f"{telemetry_timeout:.0f}s: {', '.join(missing)}. "
                "Check that dpmd is running on the target host(s)."
            )

    if reverse:
        waves = list(reversed(waves))

    total = len(waves)
    p.info(f"{mode}: {script['name']} ({total} waves, {len(groups)} groups)")

    failed_groups: List[Tuple[str, List[str]]] = []
    for i, wave in enumerate(waves, 1):
        p.info(f"  Wave {i}/{total}: {', '.join(wave)}")

        if reverse:
            for g in wave:
                procs = _fan_out_group(client, g, client.stop_group)
                p.info(f"    Stopping {g} ({len(procs)} processes)")
            for g in wave:
                ok, failed = _wait_group(client, g, timeout, running=False)
                if not ok:
                    p.warn(
                        f"    TIMEOUT: {g} not fully stopped: {', '.join(failed)}"
                    )
                    failed_groups.append((g, failed))
        else:
            for g in wave:
                procs = _fan_out_group(client, g, client.start_group)
                p.info(f"    Starting {g} ({len(procs)} processes)")
            for g in wave:
                ok, failed = _wait_group(client, g, timeout, running=True)
                if not ok:
                    dependents: List[str] = []
                    for later_wave in waves[i:]:
                        for lg in later_wave:
                            if g in groups[lg].get("requires", []):
                                dependents.append(lg)
                    if dependents:
                        p.warn(f"    FAILED: {g} not running: {', '.join(failed)}")
                        p.warn(f"    Aborting: {', '.join(dependents)} require {g}")
                        return False, (
                            f"Group '{g}' failed to start.\n"
                            f"Not running: {', '.join(failed)}\n\n"
                            f"Required by: {', '.join(dependents)}"
                        )
                    p.warn(
                        f"    FAILED: {g} not running: {', '.join(failed)} "
                        "(no hard dependents, continuing)"
                    )
                    failed_groups.append((g, failed))

    if failed_groups:
        details = "\n".join(f"  {g}: {', '.join(f)}" for g, f in failed_groups)
        return True, f"{mode} finished with warnings:\n{details}"
    return True, f"{mode} complete."
