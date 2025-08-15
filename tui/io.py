"""
Save / load process specs as YAML and create processes via Controller.

Spec format (dict or list of dicts):
  - name: string
  - host: string
  - command: string
  - group: string (optional)
  - auto_restart: bool (optional)
  - realtime: bool (optional)
"""
from __future__ import annotations
import os
import yaml
from typing import Dict, List, Tuple, Any


def save_process_spec(path: str, spec: Dict[str, Any], append: bool = False) -> None:
    """
    Save a single process spec to `path` as YAML.
    If append=True and the file contains a list, the spec is appended to the list.
    Otherwise the file is overwritten with either a single dict (append=False)
    or a list containing the single dict (append=True).
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if append and os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except Exception:
            data = None

        if isinstance(data, list):
            data.append(spec)
            with open(path, "w") as f:
                yaml.safe_dump(data, f)
            return
        elif isinstance(data, dict):
            # convert to list
            new = [data, spec]
            with open(path, "w") as f:
                yaml.safe_dump(new, f)
            return
        # fallback: overwrite with list
        with open(path, "w") as f:
            yaml.safe_dump([spec], f)
        return

    # default: write single spec (not a list)
    with open(path, "w") as f:
        yaml.safe_dump(spec, f)


def load_process_specs(path: str) -> List[Dict[str, Any]]:
    """
    Load process specs from YAML file. Returns a list of spec dicts.
    Accepts either a single dict or a list of dicts in the file.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    raise ValueError("Unsupported YAML format for process specs")


def load_and_create(path: str, controller) -> Tuple[List[str], List[Tuple[Dict[str, Any], str]]]:
    """
    Load specs from `path` and call controller.create_proc(...) for each.

    Returns a tuple (created_names, errors) where:
      - created_names: list of "<name>@<host>" for successful creates
      - errors: list of (spec, error_message) for failures
    """
    created = []
    errors: List[Tuple[Dict[str, Any], str]] = []
    specs = load_process_specs(path)
    for spec in specs:
        try:
            name = spec.get("name")
            host = spec.get("host")
            command = spec.get("command")
            group = spec.get("group", "")
            auto_restart = bool(spec.get("auto_restart", False))
            realtime = bool(spec.get("realtime", False))

            if not name or not host or not command:
                raise ValueError("spec missing required fields: name, host, command")

            controller.create_proc(name, command, group, host, auto_restart, realtime)
            created.append(f"{name}@{host}")
        except Exception as e:
            errors.append((spec, str(e)))
    return created, errors


def save_all_process_specs(path: str, controller, append: bool = False) -> Tuple[int, int]:
    """
    Save all current processes known to `controller` into `path` as a YAML list.

    - path: destination YAML file
    - controller: Controller instance with .procs property (dict procname->proc)
    - append: if True and file exists, append to existing list (or convert dict->list)

    Returns (written, skipped) where skipped is number of processes missing required fields.
    """
    procs = {}
    try:
        procs = controller.procs  # thread-safe property
    except Exception:
        procs = {}

    def _get_first(p, names):
        for n in names:
            v = getattr(p, n, None)
            if v is not None:
                return v
        return None

    specs: List[Dict[str, Any]] = []
    skipped = 0
    for p in procs.values():
        # If the proc carries an explicit spec dict, prefer that
        spec_src = getattr(p, "spec", None)
        if isinstance(spec_src, dict):
            name = spec_src.get("name", "") or ""
            host = spec_src.get("host", "") or ""
            command = spec_src.get("command", "") or spec_src.get("cmd", "") or ""
            group = spec_src.get("group", "") or ""
            auto_restart = bool(spec_src.get("auto_restart", spec_src.get("restart", False)))
            realtime = bool(spec_src.get("realtime", False))
        else:
            # try many common attribute names
            name = _get_first(p, ("name", "proc_name", "id")) or ""
            host = _get_first(p, ("hostname", "host", "node")) or ""
            command = _get_first(p, ("proc_command", "command", "cmd", "exec", "cmdline", "argv", "args")) or ""
            group = _get_first(p, ("group", "proc_group")) or ""
            auto_restart = bool(_get_first(p, ("auto_restart", "restart")))
            realtime = bool(_get_first(p, ("realtime", "real_time")))

        # if command is a list (argv/args), join it
        if isinstance(command, (list, tuple)):
            try:
                command = " ".join(str(x) for x in command)
            except Exception:
                command = str(command)

        # final normalization to strings/bools
        name = str(name) if name is not None else ""
        host = str(host) if host is not None else ""
        command = str(command) if command is not None else ""
        group = str(group) if group is not None else ""
        auto_restart = bool(auto_restart)
        realtime = bool(realtime)

        if not (name and host and command):
            skipped += 1
            continue

        specs.append(
            {
                "name": name,
                "host": host,
                "command": command,
                "group": group,
                "auto_restart": auto_restart,
                "realtime": realtime,
            }
        )

    # Ensure directory exists
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if append and os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except Exception:
            data = None

        if isinstance(data, list):
            data.extend(specs)
            with open(path, "w") as f:
                yaml.safe_dump(data, f)
        elif isinstance(data, dict):
            new = [data] + specs
            with open(path, "w") as f:
                yaml.safe_dump(new, f)
        else:
            with open(path, "w") as f:
                yaml.safe_dump(specs, f)
    else:
        # write list of specs (even if single) for clarity when loading multiple
        with open(path, "w") as f:
            yaml.safe_dump(specs, f)

    return len(specs), skipped


def save_current_processes(path: str, controller, append: bool = False) -> Tuple[int, int]:
    """
    Convenience wrapper around save_all_process_specs.
    """
    return save_all_process_specs(path, controller, append=append)