"""
Save / load process specs as YAML and create processes via Controller.

Shared module used by both GUI and TUI (keeps GUI independent of dpm.tui.*).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import yaml


def save_process_spec(path: str, spec: Dict[str, Any], append: bool = False) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if append and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            data = None

        if isinstance(data, list):
            data.append(spec)
            out = data
        elif isinstance(data, dict):
            out = [data, spec]
        else:
            out = [spec]
    else:
        out = spec

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, sort_keys=False)


def load_process_specs(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    raise ValueError(
        "Unsupported YAML format for process specs (expected dict or list of dicts)"
    )


def load_and_create(
    path: str, controller
) -> Tuple[List[str], List[Tuple[Dict[str, Any], str]]]:
    created: List[str] = []
    errors: List[Tuple[Dict[str, Any], str]] = []

    specs = load_process_specs(path)
    for spec in specs:
        try:
            name = spec.get("name")
            host = spec.get("host")
            exec_command = spec.get("exec_command")
            group = spec.get("group", "")
            auto_restart = bool(spec.get("auto_restart", False))
            realtime = bool(spec.get("realtime", False))

            if not name or not host or not exec_command:
                raise ValueError(
                    "spec missing required fields: name, host, exec_command"
                )

            controller.create_proc(
                name, exec_command, group, host, auto_restart, realtime
            )
            created.append(f"{name}@{host}")
        except Exception as e:
            errors.append((spec, str(e)))

    return created, errors


def save_all_process_specs(
    path: str, controller, append: bool = False
) -> Tuple[int, int]:
    """
    Save all processes known to controller into a YAML list.
    Returns (written, skipped).
    """
    try:
        procs = controller.procs  # snapshot dict
    except Exception:
        procs = {}

    specs: List[Dict[str, Any]] = []
    skipped = 0

    for p in procs.values():
        name = getattr(p, "name", "") or ""
        host = getattr(p, "hostname", "") or ""
        exec_command = getattr(p, "exec_command", "") or ""
        group = getattr(p, "group", "") or ""
        auto_restart = bool(getattr(p, "auto_restart", False))
        realtime = bool(getattr(p, "realtime", False))

        if not (name and host and exec_command):
            skipped += 1
            continue

        specs.append(
            {
                "name": name,
                "host": host,
                "exec_command": exec_command,
                "group": group,
                "auto_restart": auto_restart,
                "realtime": realtime,
            }
        )

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if append and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f)
        except Exception:
            existing = None

        if isinstance(existing, list):
            existing.extend(specs)
            out = existing
        elif isinstance(existing, dict):
            out = [existing] + specs
        else:
            out = specs
    else:
        out = specs

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, sort_keys=False)

    return len(specs), skipped


def save_current_processes(
    path: str, controller, append: bool = False
) -> Tuple[int, int]:
    return save_all_process_specs(path, controller, append=append)
