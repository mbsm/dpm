from pathlib import Path
import subprocess
import time
import os
import getpass

"""Helpers to spawn/stop a local node process for GUI/TUI.

Behavior:
- Locate installation/repo root by searching upwards for 'dpm.yaml' or 'node/node.py'.
- Create logs/ under that root and write node stdout/stderr to a timestamped file.
- Maintain an in-process stack of spawned subprocesses so stop_last_spawned_node() can terminate the most
  recently spawned one.

This module is intentionally self-contained so both the GUI and TUI can reuse the same logic.
"""

_spawned = []  # list of tuples (Popen, logfile_path, fileobj)


def _find_root(max_levels=8):
    # Start from the directory containing this file
    p = Path(__file__).resolve().parent
    for _ in range(max_levels):
        # Look for repository-style config or an installed layout
        if (p / "dpm.yaml").exists():
            return str(p)
        if (p / "node" / "node.py").exists():
            return str(p)
        p = p.parent
    # fallback to /opt/dpm if present
    if Path("/opt/dpm").exists():
        return "/opt/dpm"
    # last resort: package parent directories (best-effort)
    return str(Path(__file__).resolve().parents[3])


def _ensure_writable_dir(d: str) -> bool:
    try:
        os.makedirs(d, exist_ok=True)
        test_path = os.path.join(d, ".write_test")
        with open(test_path, "a"):
            pass
        try:
            os.remove(test_path)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _pick_logs_dir(root: str) -> str:
    # 1) Prefer logs under the root if writable
    cand1 = os.path.join(root, "logs")
    if _ensure_writable_dir(cand1):
        return cand1
    # 2) XDG cache directory under the user
    xdg_cache = os.getenv("XDG_CACHE_HOME", os.path.join(Path.home(), ".cache"))
    cand2 = os.path.join(xdg_cache, "dpm", "logs")
    if _ensure_writable_dir(cand2):
        return cand2
    # 3) Fall back to /tmp per-user directory
    user = os.getenv("USER") or getpass.getuser() or "user"
    cand3 = os.path.join("/tmp", f"dpm-logs-{user}")
    if _ensure_writable_dir(cand3):
        return cand3
    # 4) As a last resort, use current working directory
    cand4 = os.path.join(os.getcwd(), "logs")
    if _ensure_writable_dir(cand4):
        return cand4
    # give up and return root (may fail on open, caller will raise)
    return cand1


def spawn_local_node():
    """Spawn a local node process and return (pid, logfile_path).

    Raises an exception on failure.
    """
    root = _find_root()
    logs_dir = _pick_logs_dir(root)
    ts = int(time.time())
    logfile = os.path.join(logs_dir, f"node-{ts}.log")
    logf = open(logfile, "a")

    node_path = os.path.join(root, "node", "node.py")
    if not os.path.isfile(node_path):
        logf.close()
        raise FileNotFoundError(f"node.py not found at expected location: {node_path}")

    # Ensure PYTHONPATH includes the install root so imports resolve
    env = os.environ.copy()
    env_py = env.get("PYTHONPATH", "")
    if root not in env_py.split(":"):
        env["PYTHONPATH"] = f"{root}:{env_py}" if env_py else root

    proc = subprocess.Popen(
        ["/usr/bin/env", "python3", node_path],
        stdout=logf,
        stderr=logf,
        cwd=root,
        close_fds=True,
        env=env,
    )
    _spawned.append((proc, logfile, logf))
    return proc.pid, logfile


def stop_last_spawned_node(timeout: float = 3.0):
    """Stop the last spawned node. Returns True if terminated, False if killed, raises if none."""
    if not _spawned:
        raise RuntimeError("No spawned nodes to stop")
    proc, logfile, logf = _spawned.pop()
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
            return True
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
            return False
    finally:
        try:
            logf.close()
        except Exception:
            pass


def list_spawned():
    """Return a copy of spawned entries as (pid, logfile) tuples."""
    return [(p.pid, lf) for (p, lf, _f) in _spawned]
