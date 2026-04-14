"""YAML launch script parser and executor for ordered multi-host orchestration."""

import sys
import time
from typing import Any, Dict, List

import yaml

from dpm.cli.wait import wait_for_state


def parse_launch_script(path: str) -> Dict[str, Any]:
    """Parse a YAML launch script file.

    Returns dict with keys: name, timeout, steps.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Launch script must be a YAML dict, got {type(data).__name__}")

    return {
        "name": data.get("name", path),
        "timeout": float(data.get("timeout", 30)),
        "steps": data.get("steps", []),
    }


def reverse_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reverse a launch script's steps for shutdown.

    Transformations:
      start: X -> stop: X
      stop: X -> start: X
      wait_running: {...} -> wait_stopped: {...}
      wait_stopped: {...} -> wait_running: {...}
      sleep: N -> sleep: N (preserved)
      create: {...} -> skipped (don't delete on shutdown)
    """
    reversed_out = []
    for step in reversed(steps):
        if "start" in step:
            reversed_out.append({"stop": step["start"]})
        elif "stop" in step:
            reversed_out.append({"start": step["stop"]})
        elif "wait_running" in step:
            reversed_out.append({"wait_stopped": step["wait_running"]})
        elif "wait_stopped" in step:
            reversed_out.append({"wait_running": step["wait_stopped"]})
        elif "sleep" in step:
            reversed_out.append({"sleep": step["sleep"]})
        # create steps are skipped on shutdown
    return reversed_out


def _parse_name_at_host(value: str):
    """Split 'name@host' into (name, host)."""
    if "@" not in value:
        raise ValueError(f"Expected name@host, got '{value}'")
    name, host = value.rsplit("@", 1)
    if not name or not host:
        raise ValueError(f"Expected name@host, got '{value}'")
    return name, host


def execute_step(supervisor, step: Dict[str, Any], default_timeout: float) -> bool:
    """Execute a single launch step. Returns True on success, False on failure."""

    if "start" in step:
        name, host = _parse_name_at_host(step["start"])
        supervisor.start_proc(name, host)
        return True

    if "stop" in step:
        name, host = _parse_name_at_host(step["stop"])
        supervisor.stop_proc(name, host)
        return True

    if "sleep" in step:
        time.sleep(float(step["sleep"]))
        return True

    if "wait_running" in step:
        conf = step["wait_running"]
        targets = conf.get("targets", [])
        timeout = float(conf.get("timeout", default_timeout))
        for target in targets:
            name, host = _parse_name_at_host(target)
            if not wait_for_state(supervisor, name, host, target="R", timeout=timeout):
                print(f"  TIMEOUT waiting for {target} to reach Running", file=sys.stderr)
                return False
        return True

    if "wait_stopped" in step:
        conf = step["wait_stopped"]
        targets = conf.get("targets", [])
        timeout = float(conf.get("timeout", default_timeout))
        for target in targets:
            name, host = _parse_name_at_host(target)
            if not wait_for_state(supervisor, name, host, not_target="R", timeout=timeout):
                print(f"  TIMEOUT waiting for {target} to stop", file=sys.stderr)
                return False
        return True

    if "create" in step:
        spec = step["create"]
        supervisor.create_proc(
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
        )
        return True

    print(f"  Unknown step type: {step}", file=sys.stderr)
    return False


def run_launch(supervisor, path: str, reverse: bool = False) -> int:
    """Execute a launch script. Returns exit code (0=success)."""
    script = parse_launch_script(path)
    steps = script["steps"]
    default_timeout = script["timeout"]

    if reverse:
        steps = reverse_steps(steps)

    mode = "Shutdown" if reverse else "Launch"
    print(f"{mode}: {script['name']} ({len(steps)} steps)")

    for i, step in enumerate(steps, 1):
        # Format step for display
        step_desc = next(iter(step.items()))
        print(f"  [{i}/{len(steps)}] {step_desc[0]}: {step_desc[1]}")

        ok = execute_step(supervisor, step, default_timeout)
        if not ok:
            print(f"\nFailed at step {i}/{len(steps)}. Stopping.", file=sys.stderr)
            return 1

    print(f"\n{mode} complete.")
    return 0
