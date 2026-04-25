# Ultrareview — main — 2026-04-24

- **Scope reviewed:** 28 files changed, 845 insertions(+), 617 deletions(-)
- **Cloud session:** https://claude.ai/code/session_01Ax8iJhKFoqHEyrkZg4BZT3
- **Findings:** 1

---

## bug_001 — Missing `dpm.operations` and `dpmd.limits` modules break all imports

- **Severity:** normal (locally false-positive — see "Local status" below)
- **File:** `src/dpmd/daemon.py:23` (and other importers)

### Reviewer's report (verbatim)

> **Critical: PR ships with two missing module files** — `src/dpm/operations.py` and `src/dpmd/limits.py` are referenced by unconditional top-level imports but were never added to the changeset. This makes the package non-importable: `from dpmd.daemon import Daemon` raises `ModuleNotFoundError: No module named 'dpmd.limits'` and `from dpm.cli.launch import parse_launch_file` raises `ModuleNotFoundError: No module named 'dpm.operations'`. The dpmd binary cannot start, the `dpm launch`/`shutdown` CLI commands and GUI Launch menu fail, `dpm move` errors at first invocation, and the test suite fails at collection. Fix: add the two missing module files.

#### Importers identified

- `src/dpmd/daemon.py:23` — `from dpmd.limits import MAX_OUTPUT_BUFFER, MAX_OUTPUT_CHUNK`
- `src/dpmd/processes.py:30` — `from dpmd.limits import MAX_OUTPUT_BUFFER`
- `src/dpmd/telemetry.py:26` — `from dpmd.limits import MAX_OUTPUT_CHUNK`
- `src/dpm/cli/launch.py:12` — re-exports from `dpm.operations`
- Lazy importers: `src/dpm/cli/commands.py` (`cmd_move`, `_run_launch_script`), `src/dpm/gui/main_window.py` (`_move_proc_direct`, `_run_launch_file`, `_launch_worker`)
- Tests: `test_cli.py`, `test_launch.py`, `test_daemon_command_handler.py`, `test_daemon_output.py`, `test_client_handlers.py`, `test_client_output_delta.py`, `test_stop_signal.py`

#### Suggested fix (from the reviewer)

- `src/dpmd/limits.py` exporting `MAX_OUTPUT_BUFFER = 2 * 1024 * 1024` and `MAX_OUTPUT_CHUNK = 64 * 1024`
- `src/dpm/operations.py` containing `StdoutProgress`, `CallbackProgress`, `move_process`, `run_launch`, `parse_launch_file`, `resolve_waves`, `_validate_group_refs`, `_fan_out_group`, `_procs_in_group`, `_wait_group`, `wait_for_state`, `_create_processes_from_script`

### Local status (post-review verification)

Both files **exist on disk** in the working tree but are **untracked** (`git status` shows `?? src/dpm/operations.py` and `?? src/dpmd/limits.py`, plus `?? tests/unit/test_operations.py`).

```
-rw-rw-r--  720  src/dpmd/limits.py
-rw-rw-r-- 13745 src/dpm/operations.py
```

Ultrareview bundles tracked changes only — the cloud agents saw the imports but not the new modules, hence the "missing files" finding. Everything imports and runs locally because the files are present.

**Action:** `git add src/dpm/operations.py src/dpmd/limits.py tests/unit/test_operations.py` before committing this branch. Without that, the commit/push reproduces exactly the failure described above.
