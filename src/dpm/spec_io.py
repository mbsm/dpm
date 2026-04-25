"""
Save / load process specs as YAML and create processes via Client.

Shared module used by both GUI and CLI (keeps GUI independent of dpm.cli.*).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import yaml


def extract_proc_spec(proc) -> Dict[str, Any]:
    """Extract process spec fields from a proc_info_t message or similar object.

    Shared by CLI move, GUI move, and save operations to avoid field duplication.
    """
    return {
        "exec_command": getattr(proc, "exec_command", "") or "",
        "group": getattr(proc, "group", "") or "",
        "auto_restart": bool(getattr(proc, "auto_restart", False)),
        "realtime": bool(getattr(proc, "realtime", False)),
        "rt_priority": int(getattr(proc, "rt_priority", 0) or 0),
        "isolated": bool(getattr(proc, "isolated", False)),
        "work_dir": getattr(proc, "work_dir", "") or "",
        "cpuset": getattr(proc, "cpuset", "") or "",
        "cpu_limit": float(getattr(proc, "cpu_limit", 0.0) or 0.0),
        "mem_limit": int(getattr(proc, "mem_limit", 0) or 0),
    }


def _merge_and_write(path: str, new_items: List[Dict[str, Any]], append: bool) -> None:
    """Write new_items to a YAML file, merging with existing content when append=True."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if append and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            existing = None

        if isinstance(existing, list):
            out = existing + new_items
        elif isinstance(existing, dict):
            out = [existing] + new_items
        else:
            out = new_items
    else:
        out = new_items

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, sort_keys=False)


def save_process_spec(path: str, spec: Dict[str, Any], append: bool = False) -> None:
    _merge_and_write(path, [spec], append=append)


def _validate_spec(spec: Dict[str, Any]) -> None:
    """Raise ValueError if a spec entry has wrong types or missing required fields."""
    for field in ("name", "host", "exec_command"):
        val = spec.get(field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"spec field '{field}' must be a non-empty string, got {val!r}")
    for field in ("group", "work_dir", "cpuset"):
        val = spec.get(field, "")
        if not isinstance(val, str):
            raise ValueError(f"spec field '{field}' must be a string, got {val!r}")
    for field in ("auto_restart", "realtime", "isolated"):
        val = spec.get(field, False)
        if not isinstance(val, bool):
            raise ValueError(f"spec field '{field}' must be a boolean, got {val!r}")


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
    path: str, client
) -> Tuple[List[str], List[Tuple[Dict[str, Any], str]]]:
    created: List[str] = []
    errors: List[Tuple[Dict[str, Any], str]] = []

    specs = load_process_specs(path)
    for spec in specs:
        try:
            _validate_spec(spec)
            name = spec["name"]
            host = spec["host"]
            exec_command = spec["exec_command"]
            group = spec.get("group", "")
            auto_restart = bool(spec.get("auto_restart", False))
            realtime = bool(spec.get("realtime", False))
            rt_priority = int(spec.get("rt_priority", 0) or 0)
            isolated = bool(spec.get("isolated", False))

            client.create_proc(
                name, exec_command, group, host, auto_restart, realtime,
                rt_priority=rt_priority,
                work_dir=spec.get("work_dir", ""),
                cpuset=str(spec.get("cpuset", "")),
                cpu_limit=float(spec.get("cpu_limit", 0.0)),
                mem_limit=int(spec.get("mem_limit", 0)),
                isolated=isolated,
            )
            created.append(f"{name}@{host}")
        except Exception as e:
            errors.append((spec, str(e)))

    return created, errors


def save_all_process_specs(
    path: str, client, append: bool = False
) -> Tuple[int, int]:
    """
    Save all processes known to client into a YAML list.
    Returns (written, skipped).
    """
    try:
        procs = client.procs  # snapshot dict
    except AttributeError:
        procs = {}

    specs: List[Dict[str, Any]] = []
    skipped = 0

    for p in procs.values():
        name = getattr(p, "name", "") or ""
        host = getattr(p, "hostname", "") or ""
        exec_command = getattr(p, "exec_command", "") or ""

        if not (name and host and exec_command):
            skipped += 1
            continue

        spec = extract_proc_spec(p)
        spec["name"] = name
        spec["host"] = host
        specs.append(spec)

    if not specs:
        return 0, skipped
    _merge_and_write(path, specs, append=append)
    return len(specs), skipped
