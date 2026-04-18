"""Integration tests: real subprocesses managed by a real Daemon."""

import time

import psutil
import pytest

from dpm.constants import STATE_KILLED, STATE_READY, STATE_RUNNING
from dpmd.processes import (
    create_process,
    delete_process,
    monitor_process,
    start_process,
    stop_process,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_agent(config_path):
    """Daemon backed by real LCM — one instance for all integration tests."""
    from dpmd.daemon import Daemon
    agent = Daemon(config_file=config_path)
    yield agent
    # Cleanup: stop any processes left running
    for name in list(agent.processes.keys()):
        try:
            stop_process(agent, name)
            delete_process(agent, name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------

def test_create_start_stop_delete(real_agent):
    create_process(real_agent, "it_sleep", "sleep 100", False, False, "integration")

    start_process(real_agent, "it_sleep")
    assert real_agent.processes["it_sleep"].state == STATE_RUNNING
    assert real_agent.processes["it_sleep"].proc is not None

    pid = real_agent.processes["it_sleep"].proc.pid
    assert psutil.pid_exists(pid)

    stop_process(real_agent, "it_sleep")
    assert real_agent.processes["it_sleep"].state in (STATE_READY, STATE_KILLED)
    assert real_agent.processes["it_sleep"].proc is None

    # PID should be gone (or zombie which psutil also handles)
    try:
        p = psutil.Process(pid)
        assert p.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        pass  # cleanly reaped — correct

    delete_process(real_agent, "it_sleep")
    assert "it_sleep" not in real_agent.processes


# ---------------------------------------------------------------------------
# Auto-restart
# ---------------------------------------------------------------------------

def test_auto_restart_on_natural_exit(real_agent):
    """A fast-exiting process with auto_restart=True should be restarted by monitor."""
    create_process(real_agent, "it_echo", "echo hello", True, False, "integration")
    start_process(real_agent, "it_echo")

    # Wait for 'echo hello' to finish naturally
    time.sleep(0.5)

    # monitor_process detects the exit and restarts
    monitor_process(real_agent, "it_echo")

    assert real_agent.processes["it_echo"].state == STATE_RUNNING

    # Clean up
    real_agent.processes["it_echo"].auto_restart = False
    stop_process(real_agent, "it_echo")
    delete_process(real_agent, "it_echo")


# ---------------------------------------------------------------------------
# Stop kills the process group (children too)
# ---------------------------------------------------------------------------

def test_stop_kills_process_group(real_agent):
    """Stopping a parent should also kill any child processes it spawned."""
    create_process(
        real_agent,
        "it_parent", "bash -c 'sleep 1000 & wait'", False, False, "integration"
    )
    start_process(real_agent, "it_parent")
    time.sleep(0.3)  # let bash fork the child

    proc = real_agent.processes["it_parent"].proc
    parent_pid = proc.pid

    # Collect child PIDs before stopping
    try:
        parent_ps = psutil.Process(parent_pid)
        children = parent_ps.children(recursive=True)
        child_pids = [c.pid for c in children]
    except psutil.NoSuchProcess:
        child_pids = []

    stop_process(real_agent, "it_parent")

    # Parent should be gone
    try:
        p = psutil.Process(parent_pid)
        assert p.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        pass

    # Children should also be gone
    for cpid in child_pids:
        try:
            cp = psutil.Process(cpid)
            assert cp.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
        except psutil.NoSuchProcess:
            pass  # gone — correct

    delete_process(real_agent, "it_parent")


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------

def test_stdout_captured_in_buffer(real_agent):
    """Output written by a running process is drained into proc_info['stdout']."""
    # Use a process that produces output while it's still alive so monitor_process
    # drains the running-path (stdout buffer), not the exit-path (errors field).
    create_process(
        real_agent,
        "it_output", "bash -c 'echo captured_line; sleep 2'", False, False, "integration"
    )
    start_process(real_agent, "it_output")

    # Let the echo line be produced and picked up by the reader thread
    time.sleep(0.3)

    # monitor_process while still running → drains stdout_lines into proc_info["stdout"]
    monitor_process(real_agent, "it_output")

    assert "captured_line" in real_agent.processes["it_output"].stdout

    stop_process(real_agent, "it_output")
    delete_process(real_agent, "it_output")


# ---------------------------------------------------------------------------
# Double-start guard
# ---------------------------------------------------------------------------

def test_double_start_is_noop(real_agent):
    create_process(real_agent, "it_double", "sleep 100", False, False, "integration")
    start_process(real_agent, "it_double")
    pid_first = real_agent.processes["it_double"].proc.pid

    start_process(real_agent, "it_double")  # should be a no-op
    pid_second = real_agent.processes["it_double"].proc.pid

    assert pid_first == pid_second  # same process, no re-spawn

    stop_process(real_agent, "it_double")
    delete_process(real_agent, "it_double")
