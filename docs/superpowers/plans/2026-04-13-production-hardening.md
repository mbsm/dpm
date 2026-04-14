# DPM Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 production-hardening features to DPM: circuit breaker, per-process working directory, cpusets, configurable stop signal, CPU/memory cgroup limits, and YAML launch scripts.

**Architecture:** Features 1-5 extend the existing agent/supervisor/CLI/GUI pipeline. Each adds fields to the LCM command_t message and propagates them through every layer. Feature 6 (launch scripts) is a standalone CLI module that orchestrates existing supervisor methods. A new `cgroups.py` module encapsulates all cgroup v2 interaction.

**Tech Stack:** Python 3.10+, LCM, psutil, PyYAML, PyQt5, cgroups v2, pytest

**Spec:** `docs/superpowers/specs/2026-04-13-production-hardening-design.md`

---

### Task 1: Add SUSPENDED State to Constants

**Files:**
- Modify: `src/dpm/constants.py`
- Modify: `tests/unit/test_cli_formatting.py`

- [ ] **Step 1: Add SUSPENDED state to constants.py**

In `src/dpm/constants.py`, add below the existing state constants:

```python
STATE_SUSPENDED = "S"
```

And add to the `STATE_DISPLAY` dict:

```python
STATE_SUSPENDED: "Suspended",
```

- [ ] **Step 2: Add test for the new state formatting**

In `tests/unit/test_cli_formatting.py`, in the `test_format_state_codes` function, add:

```python
    assert format_state("S") == "Suspended"
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_cli_formatting.py::test_format_state_codes -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/dpm/constants.py tests/unit/test_cli_formatting.py
git commit -m "feat: add SUSPENDED state constant for circuit breaker"
```

---

### Task 2: Update LCM Schema and Regenerate Bindings

**Files:**
- Modify: `lcm/command_t.lcm`
- Regenerate: `src/dpm_msgs/command_t.py`

- [ ] **Step 1: Add 4 new fields to command_t.lcm**

Replace the full content of `lcm/command_t.lcm` with:

```
package dpm_msgs;

struct command_t
{
    // monotonically increasing sequence number for deduplication
    int64_t seq;

    // id of the command
    string  name;

    // group of the command
    string group;

    // remote host that should execute the command
    string hostname;

    // action: create_process, start_process, stop_process, delete_process, start_group, stop_group
    string action;

    // command line to execute
    string exec_command;

    // auto restart flag
    boolean auto_restart;

    // realtime priority flag
    boolean realtime;

    // working directory for the process (empty = inherit agent cwd)
    string work_dir;

    // cgroup cpuset: comma-separated core IDs, e.g. "0,1" (empty = no isolation)
    string cpuset;

    // cgroup CPU bandwidth limit in cores, e.g. 1.5 (0.0 = unlimited)
    double cpu_limit;

    // cgroup memory limit in bytes (0 = unlimited)
    int64_t mem_limit;
}
```

- [ ] **Step 2: Regenerate Python bindings**

Run: `./gen-types.sh`
Expected: No errors. `src/dpm_msgs/command_t.py` is regenerated with new fields.

- [ ] **Step 3: Verify the generated bindings have the new fields**

Run: `grep -c 'work_dir\|cpuset\|cpu_limit\|mem_limit' src/dpm_msgs/command_t.py`
Expected: Multiple matches (at least 4 in __slots__, 4 in __init__, etc.)

- [ ] **Step 4: Run existing tests to verify nothing breaks**

Run: `pytest tests/ -v --tb=short`
Expected: All 154 tests pass. The new fields have zero-value defaults so existing code is unaffected.

- [ ] **Step 5: Commit**

```bash
git add lcm/command_t.lcm src/dpm_msgs/command_t.py
git commit -m "feat: add work_dir, cpuset, cpu_limit, mem_limit to command_t LCM schema"
```

---

### Task 3: Circuit Breaker — Agent Implementation

**Files:**
- Modify: `src/dpm/agent/agent.py`
- Modify: `dpm.yaml`
- Modify: `debian/dpm.yaml` (if it exists, otherwise skip)
- Test: `tests/unit/test_circuit_breaker.py` (create)

- [ ] **Step 1: Write failing tests for the circuit breaker**

Create `tests/unit/test_circuit_breaker.py`:

```python
"""Tests for the max_restarts circuit breaker."""

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import CONFIG_PATH


@pytest.fixture
def agent_with_max_restarts(config_path):
    """Agent with max_restarts=3 and mocked LCM."""
    with patch("dpm.agent.agent.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        with patch("dpm.agent.agent.Agent.load_config") as mock_config:
            mock_config.return_value = {
                "command_channel": "DPM/commands",
                "host_info_channel": "DPM/host_info",
                "proc_outputs_channel": "DPM/proc_outputs",
                "host_procs_channel": "DPM/host_procs",
                "stop_timeout": 2,
                "monitor_interval": 1,
                "output_interval": 1,
                "host_status_interval": 1,
                "procs_status_interval": 1,
                "lcm_url": "udpm://239.255.76.67:7667?ttl=1",
                "max_restarts": 3,
                "stop_signal": "SIGINT",
            }
            from dpm.agent.agent import Agent
            a = Agent(config_file=str(CONFIG_PATH))
            yield a


def test_suspended_after_max_restarts(agent_with_max_restarts):
    """Process transitions to SUSPENDED after max_restarts failures."""
    from dpm.constants import STATE_FAILED, STATE_SUSPENDED
    agent = agent_with_max_restarts
    agent.create_process("test", "false", True, False, "grp")

    # Simulate restart_count reaching max_restarts
    agent.processes["test"]["state"] = STATE_FAILED
    agent.processes["test"]["auto_restart"] = True
    agent.processes["test"]["restart_count"] = 3
    agent.processes["test"]["last_restart_time"] = 0.0
    agent.processes["test"]["exit_code"] = 1

    # Mock the proc as not running (exited)
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"]["proc"] = mock_proc
    agent.processes["test"]["output_lock"] = MagicMock()
    agent.processes["test"]["stdout_lines"] = []
    agent.processes["test"]["stderr_lines"] = []

    agent.monitor_process("test")
    assert agent.processes["test"]["state"] == STATE_SUSPENDED


def test_restart_below_max_not_suspended(agent_with_max_restarts):
    """Process restarts normally when below max_restarts."""
    from dpm.constants import STATE_FAILED, STATE_SUSPENDED
    agent = agent_with_max_restarts
    agent.create_process("test", "false", True, False, "grp")

    agent.processes["test"]["state"] = STATE_FAILED
    agent.processes["test"]["auto_restart"] = True
    agent.processes["test"]["restart_count"] = 1  # below max of 3
    agent.processes["test"]["last_restart_time"] = 0.0
    agent.processes["test"]["exit_code"] = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"]["proc"] = mock_proc
    agent.processes["test"]["output_lock"] = MagicMock()
    agent.processes["test"]["stdout_lines"] = []
    agent.processes["test"]["stderr_lines"] = []

    with patch.object(agent, "start_process") as mock_start:
        agent.monitor_process("test")
        mock_start.assert_called_once_with("test")

    assert agent.processes["test"]["state"] != STATE_SUSPENDED


def test_manual_start_clears_suspended(agent_with_max_restarts):
    """Manual start on a SUSPENDED process resets the restart counter."""
    from dpm.constants import STATE_SUSPENDED
    agent = agent_with_max_restarts
    agent.create_process("test", "echo hi", True, False, "grp")

    agent.processes["test"]["state"] = STATE_SUSPENDED
    agent.processes["test"]["restart_count"] = 10
    agent.processes["test"]["last_restart_time"] = 99999.0

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

    assert agent.processes["test"]["restart_count"] == 0
    assert agent.processes["test"]["last_restart_time"] == 0.0


def test_unlimited_restarts_when_minus_one(agent):
    """When max_restarts is -1 (default), never suspend."""
    from dpm.constants import STATE_SUSPENDED
    agent.create_process("test", "false", True, False, "grp")

    agent.processes["test"]["auto_restart"] = True
    agent.processes["test"]["restart_count"] = 9999
    agent.processes["test"]["last_restart_time"] = 0.0
    agent.processes["test"]["exit_code"] = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"]["proc"] = mock_proc
    agent.processes["test"]["output_lock"] = MagicMock()
    agent.processes["test"]["stdout_lines"] = []
    agent.processes["test"]["stderr_lines"] = []

    with patch.object(agent, "start_process"):
        agent.monitor_process("test")

    assert agent.processes["test"]["state"] != STATE_SUSPENDED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_circuit_breaker.py -v`
Expected: FAIL — agent doesn't know about `max_restarts` or `STATE_SUSPENDED` yet.

- [ ] **Step 3: Add max_restarts to dpm.yaml**

In `dpm.yaml`, add at the end:

```yaml

# Maximum auto-restart attempts before suspending (-1 = unlimited)
max_restarts: -1

# Signal sent for graceful stop (SIGKILL escalation unchanged)
stop_signal: "SIGINT"
```

- [ ] **Step 4: Implement circuit breaker in agent.py**

In `src/dpm/agent/agent.py`, update the import from constants to include `STATE_SUSPENDED`:

```python
from dpm.constants import (
    STATE_DISPLAY,
    STATE_FAILED,
    STATE_KILLED,
    STATE_READY,
    STATE_RUNNING,
    STATE_SUSPENDED,
)
```

In `__init__` (around line 124, after `self.stop_timeout`), add:

```python
        self.max_restarts = int(self.config.get("max_restarts", -1))
```

In `monitor_process`, replace the auto-restart block (lines 815-828):

```python
            # Auto-restart only on failure (non-zero exit), with exponential backoff
            if proc_info["auto_restart"] and exit_code != 0:
                restart_count = proc_info.get("restart_count", 0)

                # Circuit breaker: suspend if max restarts exceeded
                if self.max_restarts >= 0 and restart_count >= self.max_restarts:
                    proc_info["state"] = STATE_SUSPENDED
                    logging.warning(
                        "Monitor Process: Process %s suspended after %d restart attempts.",
                        process_name, restart_count,
                    )
                    return

                elapsed = time.monotonic() - proc_info.get("last_restart_time", 0.0)
                backoff = min(2 ** restart_count, 60)
                if elapsed < backoff:
                    return  # wait for backoff period
                proc_info["restart_count"] = restart_count + 1
                proc_info["last_restart_time"] = time.monotonic()
                logging.info(
                    "Monitor Process: Restarting process %s (attempt %d, backoff %.0fs).",
                    process_name, restart_count + 1, backoff,
                )
                self.start_process(process_name)
```

In `start_process`, after the `is_running` check (around line 541-547), add a block to clear suspended state:

```python
        # Clear suspended state on manual start
        if proc_info["state"] == STATE_SUSPENDED:
            proc_info["restart_count"] = 0
            proc_info["last_restart_time"] = 0.0
            logging.info(
                "Start Process: Clearing SUSPENDED state for %s.", process_name
            )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_circuit_breaker.py -v`
Expected: All 4 tests pass.

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass (154 existing + 4 new).

- [ ] **Step 7: Commit**

```bash
git add src/dpm/agent/agent.py dpm.yaml tests/unit/test_circuit_breaker.py
git commit -m "feat: add circuit breaker — max_restarts config with SUSPENDED state"
```

---

### Task 4: Configurable Stop Signal

**Files:**
- Modify: `src/dpm/agent/agent.py`
- Test: `tests/unit/test_stop_signal.py` (create)

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_stop_signal.py`:

```python
"""Tests for configurable stop signal."""

import signal
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import CONFIG_PATH


@pytest.fixture
def agent_with_sigint(config_path):
    """Agent configured with stop_signal=SIGINT."""
    with patch("dpm.agent.agent.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        with patch("dpm.agent.agent.Agent.load_config") as mock_config:
            mock_config.return_value = {
                "command_channel": "DPM/commands",
                "host_info_channel": "DPM/host_info",
                "proc_outputs_channel": "DPM/proc_outputs",
                "host_procs_channel": "DPM/host_procs",
                "stop_timeout": 2,
                "monitor_interval": 1,
                "output_interval": 1,
                "host_status_interval": 1,
                "procs_status_interval": 1,
                "lcm_url": "udpm://239.255.76.67:7667?ttl=1",
                "max_restarts": -1,
                "stop_signal": "SIGINT",
            }
            from dpm.agent.agent import Agent
            a = Agent(config_file=str(CONFIG_PATH))
            yield a


def test_stop_signal_parsed_from_config(agent_with_sigint):
    assert agent_with_sigint.stop_signal == signal.SIGINT


def test_stop_sends_configured_signal(agent_with_sigint):
    """stop_process sends the configured signal, not hardcoded SIGTERM."""
    agent = agent_with_sigint
    agent.create_process("test", "sleep 999", False, False, "grp")

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None
    mock_proc.returncode = 0
    agent.processes["test"]["proc"] = mock_proc
    agent.processes["test"]["state"] = "R"

    with patch.object(agent, "_kill_process_group", return_value=True) as mock_kill:
        agent.stop_process("test")
        mock_kill.assert_any_call(12345, signal.SIGINT)


def test_stop_signal_defaults_to_sigint(agent):
    """Default agent (from dpm.yaml without stop_signal key) defaults to SIGINT."""
    assert agent.stop_signal == signal.SIGINT


def test_invalid_stop_signal_falls_back(config_path):
    """Invalid signal name falls back to SIGINT."""
    with patch("dpm.agent.agent.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        with patch("dpm.agent.agent.Agent.load_config") as mock_config:
            mock_config.return_value = {
                "command_channel": "DPM/commands",
                "host_info_channel": "DPM/host_info",
                "proc_outputs_channel": "DPM/proc_outputs",
                "host_procs_channel": "DPM/host_procs",
                "stop_timeout": 2,
                "monitor_interval": 1,
                "output_interval": 1,
                "host_status_interval": 1,
                "procs_status_interval": 1,
                "lcm_url": "udpm://239.255.76.67:7667?ttl=1",
                "max_restarts": -1,
                "stop_signal": "SIGFAKE",
            }
            from dpm.agent.agent import Agent
            a = Agent(config_file=str(CONFIG_PATH))
            assert a.stop_signal == signal.SIGINT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_stop_signal.py -v`
Expected: FAIL — `agent.stop_signal` doesn't exist yet.

- [ ] **Step 3: Implement stop signal in agent.py**

In `__init__`, after the `self.max_restarts` line, add:

```python
        # Configurable stop signal (default SIGINT; SIGKILL escalation unchanged)
        sig_name = self.config.get("stop_signal", "SIGINT")
        self.stop_signal = getattr(signal, sig_name, None)
        if self.stop_signal is None or self.stop_signal in (signal.SIGKILL, signal.SIGSTOP):
            logging.warning("Invalid stop_signal %r, falling back to SIGINT.", sig_name)
            self.stop_signal = signal.SIGINT
```

In `stop_process`, replace `signal.SIGTERM` on line 684:

```python
            sent = self._kill_process_group(proc.pid, self.stop_signal)
            if not sent:
                os.kill(proc.pid, self.stop_signal)
```

Note: also replace `proc.terminate()` with `os.kill(proc.pid, self.stop_signal)` since `proc.terminate()` always sends SIGTERM.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_stop_signal.py -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/dpm/agent/agent.py tests/unit/test_stop_signal.py
git commit -m "feat: configurable stop_signal in dpm.yaml (default SIGINT)"
```

---

### Task 5: Per-Process Working Directory — Supervisor + Agent

**Files:**
- Modify: `src/dpm/supervisor/supervisor.py`
- Modify: `src/dpm/agent/agent.py`
- Test: `tests/unit/test_work_dir.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_work_dir.py`:

```python
"""Tests for per-process working directory."""

import os
from unittest.mock import MagicMock, patch

import pytest


def test_create_stores_work_dir(agent):
    agent.create_process("test", "echo hi", False, False, "grp",
                         work_dir="/tmp")
    assert agent.processes["test"]["work_dir"] == "/tmp"


def test_create_default_work_dir(agent):
    agent.create_process("test", "echo hi", False, False, "grp")
    assert agent.processes["test"]["work_dir"] == ""


def test_start_with_valid_work_dir(agent, tmp_path):
    work_dir = str(tmp_path)
    agent.create_process("test", "echo hi", False, False, "grp",
                         work_dir=work_dir)

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        agent.start_process("test")

        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == work_dir


def test_start_with_invalid_work_dir(agent):
    from dpm.constants import STATE_FAILED
    agent.create_process("test", "echo hi", False, False, "grp",
                         work_dir="/nonexistent/path/xyz")
    agent.start_process("test")
    assert agent.processes["test"]["state"] == STATE_FAILED
    assert "does not exist" in agent.processes["test"]["errors"]


def test_start_without_work_dir_no_cwd(agent):
    agent.create_process("test", "echo hi", False, False, "grp")

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        agent.start_process("test")

        _, kwargs = mock_popen.call_args
        assert "cwd" not in kwargs or kwargs["cwd"] is None


def test_supervisor_forwards_work_dir(supervisor):
    supervisor.create_proc("test", "echo hi", "grp", "host1",
                           work_dir="/opt/robot")
    call_args = supervisor.lc_pub.publish.call_args
    # The message was encoded; just verify the method was called
    assert supervisor.lc_pub.publish.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_work_dir.py -v`
Expected: FAIL — `create_process` doesn't accept `work_dir` yet.

- [ ] **Step 3: Update supervisor.py to forward work_dir**

In `_send_command` (line 273), add `work_dir: str = ""` parameter and set it on the message:

```python
    def _send_command(
        self,
        action: str,
        name: str = "",
        hostname: str = "",
        group: str = "",
        exec_command: str = "",
        auto_restart: bool = False,
        realtime: bool = False,
        work_dir: str = "",
        cpuset: str = "",
        cpu_limit: float = 0.0,
        mem_limit: int = 0,
    ) -> None:
        msg = command_t()
        msg.action = action
        msg.name = name
        msg.hostname = hostname
        msg.group = group
        msg.exec_command = exec_command
        msg.auto_restart = bool(auto_restart)
        msg.realtime = bool(realtime)
        msg.work_dir = work_dir
        msg.cpuset = cpuset
        msg.cpu_limit = float(cpu_limit)
        msg.mem_limit = int(mem_limit)
        self._publish(msg)
```

Update `create_proc` (line 293) to accept and forward all new fields:

```python
    def create_proc(
        self,
        cmd_name: str,
        proc_cmd: str,
        group: str,
        host: str,
        auto_restart: bool = False,
        realtime: bool = False,
        work_dir: str = "",
        cpuset: str = "",
        cpu_limit: float = 0.0,
        mem_limit: int = 0,
    ) -> None:
        self._send_command("create_process", name=cmd_name, hostname=host, group=group,
                           exec_command=proc_cmd, auto_restart=auto_restart, realtime=realtime,
                           work_dir=work_dir, cpuset=cpuset, cpu_limit=cpu_limit,
                           mem_limit=mem_limit)
```

- [ ] **Step 4: Update agent.py create_process to accept and store work_dir**

Update the `create_process` signature (line 476):

```python
    def create_process(
        self, process_name, exec_command, auto_restart, realtime, group,
        work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
    ) -> None:
```

Add the new fields to the process dict (inside `self.processes[process_name] = {}`):

```python
            "work_dir": work_dir,
            "cpuset": cpuset,
            "cpu_limit": float(cpu_limit),
            "mem_limit": int(mem_limit),
```

- [ ] **Step 5: Update agent.py command_handler to forward new fields**

In `command_handler`, update the `create_process` call (line 448):

```python
            self.create_process(
                msg.name, msg.exec_command, msg.auto_restart, msg.realtime, msg.group,
                work_dir=msg.work_dir, cpuset=msg.cpuset,
                cpu_limit=msg.cpu_limit, mem_limit=msg.mem_limit,
            )
```

- [ ] **Step 6: Update agent.py start_process to use work_dir**

In `start_process`, before the `try: argv = shlex.split(...)` block (around line 568), add work_dir validation:

```python
        work_dir = proc_info.get("work_dir", "")
        if work_dir and not os.path.isdir(work_dir):
            error_msg = f"Working directory does not exist: {work_dir}"
            logging.error("Start Process: %s", error_msg)
            proc_info["state"] = STATE_FAILED
            proc_info["errors"] = error_msg
            return
```

In the `psutil.Popen()` call (line 570), add `cwd`:

```python
            popen_kwargs = dict(
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
            if work_dir:
                popen_kwargs["cwd"] = work_dir

            proc = psutil.Popen(argv, **popen_kwargs)
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/unit/test_work_dir.py -v`
Expected: All 6 tests pass.

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/dpm/agent/agent.py src/dpm/supervisor/supervisor.py tests/unit/test_work_dir.py
git commit -m "feat: per-process working directory (work_dir field)"
```

---

### Task 6: Persistence and Spec I/O for New Fields

**Files:**
- Modify: `src/dpm/agent/agent.py` (`_save_registry`, `_load_registry`)
- Modify: `src/dpm/spec_io.py` (`_validate_spec`, `save_all_process_specs`, `load_and_create`)
- Test: `tests/unit/test_spec_io.py` (add tests)

- [ ] **Step 1: Write failing tests for spec_io changes**

Add to `tests/unit/test_spec_io.py`:

```python
def test_load_and_create_forwards_new_fields(tmp_path):
    """load_and_create passes work_dir, cpuset, cpu_limit, mem_limit to supervisor."""
    spec_file = tmp_path / "procs.yaml"
    spec_file.write_text(
        "name: foo\n"
        "host: h1\n"
        "exec_command: echo\n"
        "work_dir: /opt/robot\n"
        "cpuset: '0,1'\n"
        "cpu_limit: 1.5\n"
        "mem_limit: 1073741824\n"
    )

    mock_sup = MagicMock()
    from dpm.spec_io import load_and_create
    created, errors = load_and_create(str(spec_file), mock_sup)

    assert len(created) == 1
    assert len(errors) == 0
    mock_sup.create_proc.assert_called_once_with(
        "foo", "echo", "", "h1", False, False,
        work_dir="/opt/robot", cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824,
    )


def test_save_all_includes_new_fields():
    """save_all_process_specs includes work_dir, cpuset, cpu_limit, mem_limit."""
    from unittest.mock import MagicMock, PropertyMock
    import yaml
    import tempfile, os

    mock_proc = MagicMock()
    mock_proc.name = "foo"
    mock_proc.hostname = "h1"
    mock_proc.exec_command = "echo"
    mock_proc.group = "grp"
    mock_proc.auto_restart = False
    mock_proc.realtime = False
    mock_proc.work_dir = "/opt/robot"
    mock_proc.cpuset = "0,1"
    mock_proc.cpu_limit = 1.5
    mock_proc.mem_limit = 1073741824

    mock_sup = MagicMock()
    type(mock_sup).procs = PropertyMock(return_value={("h1", "foo"): mock_proc})

    from dpm.spec_io import save_all_process_specs
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        path = f.name

    try:
        written, skipped = save_all_process_specs(path, mock_sup)
        assert written == 1
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["work_dir"] == "/opt/robot"
        assert data["cpuset"] == "0,1"
        assert data["cpu_limit"] == 1.5
        assert data["mem_limit"] == 1073741824
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_spec_io.py::test_load_and_create_forwards_new_fields tests/unit/test_spec_io.py::test_save_all_includes_new_fields -v`
Expected: FAIL

- [ ] **Step 3: Update spec_io.py**

In `_validate_spec` (line 44), add the new optional fields:

```python
    for field in ("group", "work_dir", "cpuset"):
        val = spec.get(field, "")
        if not isinstance(val, str):
            raise ValueError(f"spec field '{field}' must be a string, got {val!r}")
    for field in ("auto_restart", "realtime"):
        val = spec.get(field, False)
        if not isinstance(val, bool):
            raise ValueError(f"spec field '{field}' must be a boolean, got {val!r}")
```

In `load_and_create` (line 77), update the call to `supervisor.create_proc`:

```python
            supervisor.create_proc(
                name, exec_command, group, host, auto_restart, realtime,
                work_dir=spec.get("work_dir", ""),
                cpuset=str(spec.get("cpuset", "")),
                cpu_limit=float(spec.get("cpu_limit", 0.0)),
                mem_limit=int(spec.get("mem_limit", 0)),
            )
```

In `save_all_process_specs` (line 131), add the new fields to the spec dict:

```python
        specs.append(
            {
                "name": name,
                "host": host,
                "exec_command": exec_command,
                "group": group,
                "auto_restart": auto_restart,
                "realtime": realtime,
                "work_dir": getattr(p, "work_dir", "") or "",
                "cpuset": getattr(p, "cpuset", "") or "",
                "cpu_limit": float(getattr(p, "cpu_limit", 0.0) or 0.0),
                "mem_limit": int(getattr(p, "mem_limit", 0) or 0),
            }
        )
```

- [ ] **Step 4: Update agent _save_registry and _load_registry**

In `_save_registry` (line 321), add the new fields to each spec:

```python
            specs.append({
                "name": name,
                "exec_command": info["exec_command"],
                "group": info.get("group", ""),
                "auto_restart": info["auto_restart"],
                "realtime": info["realtime"],
                "work_dir": info.get("work_dir", ""),
                "cpuset": info.get("cpuset", ""),
                "cpu_limit": info.get("cpu_limit", 0.0),
                "mem_limit": info.get("mem_limit", 0),
            })
```

In `_load_registry` (line 375), pass new fields to `create_process`:

```python
            self.create_process(
                name,
                exec_command,
                spec.get("auto_restart", False),
                spec.get("realtime", False),
                spec.get("group", ""),
                work_dir=spec.get("work_dir", ""),
                cpuset=str(spec.get("cpuset", "")),
                cpu_limit=float(spec.get("cpu_limit", 0.0)),
                mem_limit=int(spec.get("mem_limit", 0)),
            )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_spec_io.py -v`
Expected: All tests pass (existing + 2 new).

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/dpm/agent/agent.py src/dpm/spec_io.py tests/unit/test_spec_io.py
git commit -m "feat: persist and load new process fields (work_dir, cpuset, cpu_limit, mem_limit)"
```

---

### Task 7: Cgroups v2 Module

**Files:**
- Create: `src/dpm/agent/cgroups.py`
- Test: `tests/unit/test_cgroups.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_cgroups.py`:

```python
"""Tests for cgroups v2 module (mocked filesystem)."""

import os
from unittest.mock import mock_open, patch, call, MagicMock

import pytest


def test_cgroups_available_true(tmp_path):
    """Returns True when cgroup v2 unified hierarchy is mounted and writable."""
    from dpm.agent.cgroups import cgroups_available
    dpm_dir = tmp_path / "dpm"
    dpm_dir.mkdir()
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        assert cgroups_available() is True


def test_cgroups_available_false_no_dir():
    """Returns False when cgroup dir doesn't exist."""
    from dpm.agent.cgroups import cgroups_available
    with patch("dpm.agent.cgroups.CGROUP_BASE", "/nonexistent/cgroup/path"):
        assert cgroups_available() is False


def test_setup_cgroup_creates_dir_and_writes(tmp_path):
    """setup_cgroup creates the cgroup dir and writes controller files."""
    from dpm.agent.cgroups import setup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        setup_cgroup("myproc", pid=1234, cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824)

    cgroup_dir = tmp_path / "myproc"
    assert cgroup_dir.is_dir()
    assert (cgroup_dir / "cpuset.cpus").read_text() == "0,1"
    assert (cgroup_dir / "cpu.max").read_text() == "150000 100000"
    assert (cgroup_dir / "memory.max").read_text() == "1073741824"
    assert (cgroup_dir / "cgroup.procs").read_text() == "1234"


def test_setup_cgroup_skips_unset_limits(tmp_path):
    """Only writes controller files for non-zero limits."""
    from dpm.agent.cgroups import setup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        setup_cgroup("myproc", pid=1234, cpuset="", cpu_limit=0.0, mem_limit=0)

    cgroup_dir = tmp_path / "myproc"
    assert cgroup_dir.is_dir()
    assert not (cgroup_dir / "cpuset.cpus").exists()
    assert not (cgroup_dir / "cpu.max").exists()
    assert not (cgroup_dir / "memory.max").exists()
    assert (cgroup_dir / "cgroup.procs").read_text() == "1234"


def test_cleanup_cgroup_removes_dir(tmp_path):
    """cleanup_cgroup removes the cgroup directory."""
    from dpm.agent.cgroups import cleanup_cgroup
    cgroup_dir = tmp_path / "myproc"
    cgroup_dir.mkdir()
    (cgroup_dir / "cgroup.procs").write_text("")

    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        cleanup_cgroup("myproc")

    assert not cgroup_dir.exists()


def test_cleanup_cgroup_nonexistent_is_noop(tmp_path):
    """cleanup_cgroup on nonexistent dir doesn't raise."""
    from dpm.agent.cgroups import cleanup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        cleanup_cgroup("nonexistent")  # should not raise


def test_setup_cgroup_cpu_limit_conversion(tmp_path):
    """Verify cpu_limit to cpu.max conversion: 2.0 cores = 200000 100000."""
    from dpm.agent.cgroups import setup_cgroup
    with patch("dpm.agent.cgroups.CGROUP_BASE", str(tmp_path)):
        setup_cgroup("myproc", pid=1, cpuset="", cpu_limit=2.0, mem_limit=0)

    assert (tmp_path / "myproc" / "cpu.max").read_text() == "200000 100000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_cgroups.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement cgroups.py**

Create `src/dpm/agent/cgroups.py`:

```python
"""Cgroups v2 management for DPM agent process isolation."""

import logging
import os
import shutil

# Base path for the DPM cgroup subtree. Requires Delegate=yes in the
# systemd unit so the agent's user can create child cgroups here.
CGROUP_BASE = "/sys/fs/cgroup/dpm"

# cpu.max period (microseconds) — standard 100ms scheduling period
_CPU_PERIOD = 100_000


def cgroups_available() -> bool:
    """Return True if cgroups v2 is available and the DPM subtree is writable."""
    try:
        if not os.path.isdir(CGROUP_BASE):
            # Try to create it (works if parent is delegated)
            os.makedirs(CGROUP_BASE, exist_ok=True)
        return os.access(CGROUP_BASE, os.W_OK)
    except OSError:
        return False


def setup_cgroup(
    name: str,
    pid: int,
    cpuset: str = "",
    cpu_limit: float = 0.0,
    mem_limit: int = 0,
) -> None:
    """Create a cgroup for a process and apply resource limits.

    Args:
        name: Process name (used as cgroup directory name).
        pid: PID to place in the cgroup.
        cpuset: Comma-separated core IDs (e.g. "0,1"). Empty = no restriction.
        cpu_limit: CPU bandwidth in cores (e.g. 1.5). 0.0 = unlimited.
        mem_limit: Memory limit in bytes. 0 = unlimited.

    Raises:
        OSError: If cgroup creation or writes fail.
    """
    cgroup_dir = os.path.join(CGROUP_BASE, name)
    os.makedirs(cgroup_dir, exist_ok=True)

    if cpuset:
        _write(cgroup_dir, "cpuset.cpus", cpuset)

    if cpu_limit > 0:
        quota = int(cpu_limit * _CPU_PERIOD)
        _write(cgroup_dir, "cpu.max", f"{quota} {_CPU_PERIOD}")

    if mem_limit > 0:
        _write(cgroup_dir, "memory.max", str(mem_limit))

    _write(cgroup_dir, "cgroup.procs", str(pid))

    logging.debug(
        "Cgroup setup: %s pid=%d cpuset=%r cpu_limit=%s mem_limit=%s",
        name, pid, cpuset, cpu_limit, mem_limit,
    )


def cleanup_cgroup(name: str) -> None:
    """Remove the cgroup directory for a process. Best-effort."""
    cgroup_dir = os.path.join(CGROUP_BASE, name)
    if not os.path.isdir(cgroup_dir):
        return
    try:
        # Move any remaining PIDs to parent before removing
        procs_file = os.path.join(cgroup_dir, "cgroup.procs")
        if os.path.exists(procs_file):
            parent_procs = os.path.join(CGROUP_BASE, "cgroup.procs")
            try:
                with open(procs_file, "r") as f:
                    pids = f.read().strip().split()
                if pids and os.path.exists(parent_procs):
                    for pid in pids:
                        if pid:
                            try:
                                _write(CGROUP_BASE, "cgroup.procs", pid)
                            except OSError:
                                pass
            except OSError:
                pass
        os.rmdir(cgroup_dir)
        logging.debug("Cgroup cleanup: removed %s", cgroup_dir)
    except OSError as e:
        logging.warning("Cgroup cleanup failed for %s: %s", name, e)


def _write(cgroup_dir: str, filename: str, value: str) -> None:
    """Write a value to a cgroup control file."""
    path = os.path.join(cgroup_dir, filename)
    with open(path, "w") as f:
        f.write(value)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_cgroups.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/dpm/agent/cgroups.py tests/unit/test_cgroups.py
git commit -m "feat: cgroups v2 module — setup, cleanup, availability check"
```

---

### Task 8: Integrate Cgroups into Agent Start/Stop

**Files:**
- Modify: `src/dpm/agent/agent.py`
- Modify: `debian/dpm-agent.service`
- Test: `tests/unit/test_agent_cgroups.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_agent_cgroups.py`:

```python
"""Tests for cgroup integration in agent start/stop."""

from unittest.mock import MagicMock, patch, call

import pytest


def test_start_process_calls_setup_cgroup(agent):
    """start_process calls setup_cgroup when limits are set."""
    agent.create_process("test", "echo hi", False, False, "grp",
                         cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824)

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen, \
         patch("dpm.agent.cgroups.cgroups_available", return_value=True), \
         patch("dpm.agent.cgroups.setup_cgroup") as mock_setup:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

        mock_setup.assert_called_once_with("test", 123, cpuset="0,1",
                                           cpu_limit=1.5, mem_limit=1073741824)


def test_start_process_skips_cgroup_when_no_limits(agent):
    """start_process doesn't call setup_cgroup when no limits are set."""
    agent.create_process("test", "echo hi", False, False, "grp")

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen, \
         patch("dpm.agent.cgroups.setup_cgroup") as mock_setup:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

        mock_setup.assert_not_called()


def test_start_process_continues_on_cgroup_failure(agent):
    """start_process continues if cgroup setup fails (non-fatal)."""
    from dpm.constants import STATE_RUNNING
    agent.create_process("test", "echo hi", False, False, "grp",
                         cpuset="0,1")

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen, \
         patch("dpm.agent.cgroups.cgroups_available", return_value=True), \
         patch("dpm.agent.cgroups.setup_cgroup", side_effect=OSError("permission denied")):
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

        # Process should still be running despite cgroup failure
        assert agent.processes["test"]["state"] == STATE_RUNNING


def test_stop_process_calls_cleanup_cgroup(agent):
    """stop_process calls cleanup_cgroup."""
    agent.create_process("test", "echo hi", False, False, "grp",
                         cpuset="0,1")

    mock_proc = MagicMock()
    mock_proc.pid = 123
    mock_proc.poll.return_value = None
    mock_proc.returncode = 0
    agent.processes["test"]["proc"] = mock_proc
    agent.processes["test"]["state"] = "R"

    with patch("dpm.agent.cgroups.cleanup_cgroup") as mock_cleanup, \
         patch.object(agent, "_kill_process_group", return_value=True):
        agent.stop_process("test")
        mock_cleanup.assert_called_once_with("test")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_agent_cgroups.py -v`
Expected: FAIL — agent doesn't call cgroups yet.

- [ ] **Step 3: Integrate cgroups into agent.py**

Add import at the top of `agent.py` (after the existing imports):

```python
from dpm.agent.cgroups import cgroups_available, cleanup_cgroup, setup_cgroup
```

In `start_process`, after the successful Popen and RT priority blocks (after the `except (OSError, ValueError, psutil.Error)` block at line 645, but still inside the `try`), add cgroup setup:

```python
            # Apply cgroup resource limits (cpuset, CPU, memory)
            _cpuset = proc_info.get("cpuset", "")
            _cpu_limit = proc_info.get("cpu_limit", 0.0)
            _mem_limit = proc_info.get("mem_limit", 0)
            if (_cpuset or _cpu_limit > 0 or _mem_limit > 0) and cgroups_available():
                try:
                    setup_cgroup(process_name, proc.pid,
                                 cpuset=_cpuset, cpu_limit=_cpu_limit, mem_limit=_mem_limit)
                except OSError as e:
                    logging.warning(
                        "Start Process: cgroup setup failed for %s: %s (continuing without limits)",
                        process_name, e,
                    )
```

In `stop_process`, in the `finally` block (line 729), add cleanup:

```python
        finally:
            proc_info["proc"] = None
            proc_info["ps_proc"] = None
            cleanup_cgroup(process_name)
```

In `delete_process` (line 524), add cleanup before the `del`:

```python
            cleanup_cgroup(process_name)
            del self.processes[process_name]
```

- [ ] **Step 4: Add Delegate=yes to systemd service**

In `debian/dpm-agent.service`, add `Delegate=yes` in the `[Service]` section after `Group=dpm`:

```ini
# Cgroup delegation for per-process resource limits
Delegate=yes
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_agent_cgroups.py -v`
Expected: All 4 tests pass.

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/dpm/agent/agent.py debian/dpm-agent.service tests/unit/test_agent_cgroups.py
git commit -m "feat: integrate cgroups v2 into agent start/stop with Delegate=yes"
```

---

### Task 9: CLI Flags for New Fields

**Files:**
- Modify: `src/dpm/cli/cli.py`
- Modify: `src/dpm/cli/commands.py`
- Test: `tests/unit/test_cli.py` (add tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_cli.py`:

```python
def test_argparse_create_with_new_fields():
    from dpm.cli.cli import build_parser, _resolve_args
    parser = build_parser()
    args = parser.parse_args([
        "create", "foo@host1", "--cmd", "echo hi",
        "--work-dir", "/opt/robot",
        "--cpuset", "0,1",
        "--cpu-limit", "1.5",
        "--mem-limit", "1073741824",
    ])
    args = _resolve_args(args)
    assert args.name == "foo"
    assert args.host == "host1"
    assert args.work_dir == "/opt/robot"
    assert args.cpuset == "0,1"
    assert args.cpu_limit == 1.5
    assert args.mem_limit == 1073741824


def test_create_forwards_new_fields_to_supervisor():
    from unittest.mock import MagicMock, patch
    from dpm.cli.commands import cmd_create

    mock_sup = MagicMock()
    mock_sup.hosts = {"host1": MagicMock()}
    mock_sup.procs = {}

    args = MagicMock()
    args.command = "create"
    args.name = "foo"
    args.host = "host1"
    args.cmd = "echo hi"
    args.group = "grp"
    args.auto_restart = False
    args.realtime = False
    args.work_dir = "/opt/robot"
    args.cpuset = "0,1"
    args.cpu_limit = 1.5
    args.mem_limit = 1073741824

    with patch("dpm.cli.commands.wait_for_telemetry", return_value=True), \
         patch("dpm.cli.commands.wait_for_state", return_value=True):
        cmd_create(mock_sup, args)

    mock_sup.create_proc.assert_called_once_with(
        "foo", "echo hi", "grp", "host1", False, False,
        work_dir="/opt/robot", cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_cli.py::test_argparse_create_with_new_fields tests/unit/test_cli.py::test_create_forwards_new_fields_to_supervisor -v`
Expected: FAIL

- [ ] **Step 3: Add CLI flags to build_parser in cli.py**

In `build_parser()`, update the `create` subparser (after line 118):

```python
    p_create.add_argument("--work-dir", default="", help="Working directory")
    p_create.add_argument("--cpuset", default="", help="CPU set cores (e.g. 0,1,2)")
    p_create.add_argument("--cpu-limit", type=float, default=0.0,
                          help="CPU limit in cores (e.g. 1.5)")
    p_create.add_argument("--mem-limit", type=int, default=0,
                          help="Memory limit in bytes")
```

- [ ] **Step 4: Update cmd_create in commands.py**

Replace `cmd_create` (line 174):

```python
def cmd_create(supervisor, args) -> int:
    name, host = args.name, args.host
    supervisor.create_proc(
        name, args.cmd, args.group, host, args.auto_restart, args.realtime,
        work_dir=args.work_dir, cpuset=args.cpuset,
        cpu_limit=args.cpu_limit, mem_limit=args.mem_limit,
    )

    if wait_for_telemetry(supervisor):
        confirmed = wait_for_state(supervisor, name, host, target="T", timeout=3.0)
        if confirmed:
            print(f"Created {name}@{host}")
            return 0

    print(f"Create command sent for {name}@{host}")
    return 0
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_cli.py -v`
Expected: All tests pass (existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/dpm/cli/cli.py src/dpm/cli/commands.py tests/unit/test_cli.py
git commit -m "feat: CLI flags for work-dir, cpuset, cpu-limit, mem-limit on dpm create"
```

---

### Task 10: GUI ProcessDialog Updates

**Files:**
- Modify: `src/dpm/gui/process_dialog.py`

- [ ] **Step 1: Add new fields to init_ui**

In `init_ui`, after the `self.realtime_checkbox` lines (line 33), add:

```python
        self.work_dir_input = QLineEdit()
        self.work_dir_input.setPlaceholderText("/path/to/working/dir")
        self.cpuset_input = QLineEdit()
        self.cpuset_input.setPlaceholderText("e.g. 0,1,2")
        self.cpu_limit_input = QLineEdit()
        self.cpu_limit_input.setPlaceholderText("e.g. 1.5 (cores)")
        self.mem_limit_input = QLineEdit()
        self.mem_limit_input.setPlaceholderText("e.g. 1073741824 (bytes)")
```

After the existing `addRow` calls (line 44), add:

```python
        self.form_layout.addRow("Working Dir:", self.work_dir_input)
        self.form_layout.addRow("CPU Set:", self.cpuset_input)
        self.form_layout.addRow("CPU Limit:", self.cpu_limit_input)
        self.form_layout.addRow("Mem Limit:", self.mem_limit_input)
```

- [ ] **Step 2: Update load_process_data**

In `load_process_data` (line 61), after the existing lines, add:

```python
        self.work_dir_input.setText(getattr(self.proc, "work_dir", "") or "")
        self.cpuset_input.setText(getattr(self.proc, "cpuset", "") or "")
        cpu_limit = getattr(self.proc, "cpu_limit", 0.0) or 0.0
        self.cpu_limit_input.setText(str(cpu_limit) if cpu_limit > 0 else "")
        mem_limit = getattr(self.proc, "mem_limit", 0) or 0
        self.mem_limit_input.setText(str(mem_limit) if mem_limit > 0 else "")
```

- [ ] **Step 3: Update save_process**

In `save_process` (line 72), after the existing field reads, add:

```python
        work_dir = self.work_dir_input.text().strip()
        cpuset = self.cpuset_input.text().strip()
        try:
            cpu_limit = float(self.cpu_limit_input.text().strip() or "0")
        except ValueError:
            cpu_limit = 0.0
        try:
            mem_limit = int(self.mem_limit_input.text().strip() or "0")
        except ValueError:
            mem_limit = 0
```

Update the `self.supervisor.create_proc(...)` call to include the new fields:

```python
            self.supervisor.create_proc(
                name, proc_command, group, host, auto_restart, realtime,
                work_dir=work_dir, cpuset=cpuset,
                cpu_limit=cpu_limit, mem_limit=mem_limit,
            )
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/dpm/gui/process_dialog.py
git commit -m "feat: GUI dialog fields for work_dir, cpuset, cpu_limit, mem_limit"
```

---

### Task 11: Launch Scripts Module

**Files:**
- Create: `src/dpm/cli/launch.py`
- Test: `tests/unit/test_launch.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_launch.py`:

```python
"""Tests for YAML launch script parsing and execution."""

from unittest.mock import MagicMock, patch, call

import pytest
import yaml


def _write_script(tmp_path, steps):
    path = tmp_path / "launch.yaml"
    data = {"name": "test", "timeout": 5, "steps": steps}
    path.write_text(yaml.safe_dump(data))
    return str(path)


def test_parse_launch_script(tmp_path):
    from dpm.cli.launch import parse_launch_script
    path = _write_script(tmp_path, [
        {"start": "foo@host1"},
        {"stop": "bar@host1"},
        {"sleep": 1.0},
        {"wait_running": {"targets": ["foo@host1"], "timeout": 10}},
    ])
    script = parse_launch_script(path)
    assert script["name"] == "test"
    assert script["timeout"] == 5
    assert len(script["steps"]) == 4


def test_parse_launch_script_missing_file():
    from dpm.cli.launch import parse_launch_script
    with pytest.raises(FileNotFoundError):
        parse_launch_script("/nonexistent/file.yaml")


def test_reverse_steps():
    from dpm.cli.launch import reverse_steps
    steps = [
        {"start": "a@h1"},
        {"start": "b@h1"},
        {"wait_running": {"targets": ["a@h1", "b@h1"]}},
        {"start": "c@h2"},
        {"sleep": 2.0},
    ]
    reversed_steps = reverse_steps(steps)
    assert reversed_steps == [
        {"sleep": 2.0},
        {"stop": "c@h2"},
        {"wait_stopped": {"targets": ["a@h1", "b@h1"]}},
        {"stop": "b@h1"},
        {"stop": "a@h1"},
    ]


def test_reverse_steps_skips_create():
    from dpm.cli.launch import reverse_steps
    steps = [
        {"create": {"name": "foo", "host": "h1", "cmd": "echo"}},
        {"start": "foo@h1"},
    ]
    reversed_steps = reverse_steps(steps)
    assert reversed_steps == [
        {"stop": "foo@h1"},
        # create is skipped
    ]


def test_execute_start_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"start": "foo@host1"}
    result = execute_step(sup, step, default_timeout=5)
    sup.start_proc.assert_called_once_with("foo", "host1")
    assert result is True


def test_execute_stop_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"stop": "bar@host1"}
    result = execute_step(sup, step, default_timeout=5)
    sup.stop_proc.assert_called_once_with("bar", "host1")
    assert result is True


def test_execute_sleep_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"sleep": 0.01}
    with patch("dpm.cli.launch.time.sleep") as mock_sleep:
        result = execute_step(sup, step, default_timeout=5)
        mock_sleep.assert_called_once_with(0.01)
    assert result is True


def test_execute_wait_running_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"wait_running": {"targets": ["foo@host1"], "timeout": 2}}

    with patch("dpm.cli.launch.wait_for_state", return_value=True) as mock_wait:
        result = execute_step(sup, step, default_timeout=5)
        mock_wait.assert_called_once_with(sup, "foo", "host1", target="R", timeout=2)
    assert result is True


def test_execute_wait_running_timeout():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"wait_running": {"targets": ["foo@host1"], "timeout": 2}}

    with patch("dpm.cli.launch.wait_for_state", return_value=False):
        result = execute_step(sup, step, default_timeout=5)
    assert result is False


def test_execute_create_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"create": {
        "name": "foo", "host": "h1", "cmd": "echo hi",
        "group": "grp", "auto_restart": True,
    }}
    result = execute_step(sup, step, default_timeout=5)
    sup.create_proc.assert_called_once_with(
        "foo", "echo hi", "grp", "h1", True, False,
        work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
    )
    assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_launch.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement launch.py**

Create `src/dpm/cli/launch.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_launch.py -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/dpm/cli/launch.py tests/unit/test_launch.py
git commit -m "feat: YAML launch script parser and executor"
```

---

### Task 12: Wire Launch Commands into CLI

**Files:**
- Modify: `src/dpm/cli/cli.py`
- Modify: `src/dpm/cli/commands.py`
- Test: `tests/unit/test_cli.py` (add tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_cli.py`:

```python
def test_argparse_launch():
    from dpm.cli.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["launch", "system.yaml"])
    assert args.command == "launch"
    assert args.path == "system.yaml"


def test_argparse_shutdown():
    from dpm.cli.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["shutdown", "system.yaml"])
    assert args.command == "shutdown"
    assert args.path == "system.yaml"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_cli.py::test_argparse_launch tests/unit/test_cli.py::test_argparse_shutdown -v`
Expected: FAIL

- [ ] **Step 3: Add launch/shutdown subparsers to cli.py**

In `build_parser()`, after the `logs` subparser (around line 165), add:

```python
    # dpm launch script.yaml
    p_launch = sub.add_parser("launch", help="Execute a launch script (ordered startup)")
    p_launch.add_argument("path", help="Path to YAML launch script")

    # dpm shutdown script.yaml
    p_shutdown = sub.add_parser("shutdown", help="Execute a launch script in reverse (ordered shutdown)")
    p_shutdown.add_argument("path", help="Path to YAML launch script")
```

In the `DISPATCH` dict (line 28), add:

```python
    "launch": cmd_launch,
    "shutdown": cmd_shutdown,
```

Add imports at the top of `cli.py`:

```python
from dpm.cli.commands import (
    ...
    cmd_launch,
    cmd_shutdown,
)
```

- [ ] **Step 4: Add cmd_launch and cmd_shutdown to commands.py**

Add at the end of `commands.py`:

```python
def cmd_launch(supervisor, args) -> int:
    from dpm.cli.launch import run_launch

    if not wait_for_telemetry(supervisor):
        return _no_agents()

    return run_launch(supervisor, args.path, reverse=False)


def cmd_shutdown(supervisor, args) -> int:
    from dpm.cli.launch import run_launch

    if not wait_for_telemetry(supervisor):
        return _no_agents()

    return run_launch(supervisor, args.path, reverse=True)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_cli.py -v`
Expected: All tests pass (existing + 2 new).

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/dpm/cli/cli.py src/dpm/cli/commands.py tests/unit/test_cli.py
git commit -m "feat: dpm launch / dpm shutdown CLI commands for orchestrated startup"
```

---

### Task 13: Final Integration Test and Cleanup

**Files:**
- Modify: `dpm.yaml` (verify config)
- Run: full test suite

- [ ] **Step 1: Verify dpm.yaml has all new config keys**

Confirm `dpm.yaml` contains:

```yaml
max_restarts: -1
stop_signal: "SIGINT"
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 3: Verify imports are clean**

Run: `python -c "from dpm.agent.agent import Agent; from dpm.supervisor.supervisor import Supervisor; from dpm.cli.launch import run_launch; from dpm.agent.cgroups import cgroups_available; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: DPM production hardening — circuit breaker, work_dir, cgroups, stop signal, launch scripts"
```
