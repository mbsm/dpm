"""Compatibility shim — the real logic lives in :mod:`dpm.operations`.

All new code should import from ``dpm.operations`` directly. This module
re-exports the public and commonly mocked names so pre-existing imports
(``from dpm.cli.launch import …``) and tests keep working. It also
contains the legacy helper names used by tests.
"""
from __future__ import annotations

from typing import List, Tuple

from dpm.operations import (  # noqa: F401 — public re-exports
    StdoutProgress,
    _fan_out_group,
    _procs_in_group,
    _validate_group_refs,
    _wait_group,
    parse_launch_file,
    resolve_waves,
)


def run_launch(client, path: str, reverse: bool = False) -> int:
    """CLI entry point: parse and execute a launch file with stdout progress."""
    from dpm.operations import parse_launch_file as _parse
    from dpm.operations import run_launch as _run

    script = _parse(path)
    ok, message = _run(client, script, reverse=reverse, progress=StdoutProgress())
    if message:
        print(f"\n{message}")
    return 0 if ok else 1


def _start_group(client, group_name: str) -> List[Tuple[str, str]]:
    return _fan_out_group(client, group_name, client.start_group)


def _stop_group(client, group_name: str) -> List[Tuple[str, str]]:
    return _fan_out_group(client, group_name, client.stop_group)


def _wait_group_running(client, group_name: str, timeout: float):
    return _wait_group(client, group_name, timeout, running=True)


def _wait_group_stopped(client, group_name: str, timeout: float):
    return _wait_group(client, group_name, timeout, running=False)


def _create_processes(client, processes) -> int:
    """Legacy name — mirrors operations._create_processes_from_script but with
    the StdoutProgress sink to preserve CLI output for callers that still
    import this helper directly."""
    from dpm.operations import _create_processes_from_script
    return _create_processes_from_script(client, processes, StdoutProgress())
