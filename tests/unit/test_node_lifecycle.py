"""Tests for Daemon process lifecycle state machine (no real subprocesses)."""

import pytest

from dpm.constants import STATE_FAILED, STATE_KILLED, STATE_READY, STATE_RUNNING
from dpmd.processes import (
    _group_matches,
    create_process,
    delete_process,
    start_process,
    stop_process,
)


# ---------------------------------------------------------------------------
# create_process
# ---------------------------------------------------------------------------

def test_create_sets_initial_state(agent):
    create_process(agent, "p1", "echo hi", False, False, "grp")
    p = agent.processes["p1"]
    assert p.state == STATE_READY
    assert p.proc is None
    assert p.exit_code == -1
    assert p.stdout == ""
    assert p.stderr == ""
    assert p.errors == ""
    assert p.restart_count == 0


def test_create_stores_metadata(agent):
    create_process(agent, "p1", "sleep 100", True, True, "mygroup")
    p = agent.processes["p1"]
    assert p.exec_command == "sleep 100"
    assert p.auto_restart is True
    assert p.realtime is True
    assert p.group == "mygroup"


def test_create_defaults_output_fields(agent):
    """output_lock defaults to None; stdout_lines/stderr_lines default to empty lists."""
    create_process(agent, "p1", "cmd", False, False, "")
    p = agent.processes["p1"]
    assert p.output_lock is None
    assert p.stdout_lines == []
    assert p.stderr_lines == []


def test_create_overwrites_existing_entry(agent):
    create_process(agent, "p1", "echo a", False, False, "g1")
    create_process(agent, "p1", "echo b", True, False, "g2")
    p = agent.processes["p1"]
    assert p.exec_command == "echo b"
    assert p.auto_restart is True
    assert p.group == "g2"


def test_create_stops_running_process_before_overwrite(agent):
    from unittest.mock import MagicMock, patch
    create_process(agent, "p1", "echo a", False, False, "g1")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    agent.processes["p1"].proc = fake_proc
    with patch("dpmd.processes.stop_process") as mock_stop:
        create_process(agent, "p1", "echo b", False, False, "g2")
    mock_stop.assert_called_once_with(agent, "p1")


# ---------------------------------------------------------------------------
# start_process — guard paths (no real subprocess)
# ---------------------------------------------------------------------------

def test_start_unknown_process_no_crash(agent):
    start_process(agent, "nonexistent")  # should log warning and return cleanly


def test_start_already_running_process_no_crash(agent):
    """If proc.poll() returns None (still running), start should be a no-op."""
    from unittest.mock import MagicMock
    create_process(agent, "p1", "sleep 100", False, False, "")
    # Plant a fake "running" proc
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    agent.processes["p1"].proc = fake_proc
    agent.processes["p1"].state = STATE_RUNNING
    start_process(agent, "p1")
    # Still the same fake proc — no re-spawn
    assert agent.processes["p1"].proc is fake_proc


# ---------------------------------------------------------------------------
# stop_process — guard paths
# ---------------------------------------------------------------------------

def test_stop_unknown_process_no_crash(agent):
    stop_process(agent, "nonexistent")


def test_stop_process_with_no_proc_is_noop(agent):
    create_process(agent, "p1", "cmd", False, False, "")
    # proc is None by default → should be a no-op
    stop_process(agent, "p1")
    assert agent.processes["p1"].state == STATE_READY


# ---------------------------------------------------------------------------
# delete_process — guard paths
# ---------------------------------------------------------------------------

def test_delete_unknown_process_no_crash(agent):
    delete_process(agent, "nonexistent")


def test_delete_removes_from_table(agent):
    create_process(agent, "p1", "cmd", False, False, "")
    delete_process(agent, "p1")
    assert "p1" not in agent.processes


def test_delete_calls_stop_if_proc_running(agent):
    from unittest.mock import MagicMock, patch
    create_process(agent, "p1", "sleep 10", False, False, "")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    agent.processes["p1"].proc = fake_proc
    with patch("dpmd.processes.stop_process") as mock_stop:
        delete_process(agent, "p1")
    mock_stop.assert_called_once_with(agent, "p1")


# ---------------------------------------------------------------------------
# _group_matches
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proc_group,target_group,expected", [
    ("", "", True),
    ("", "(ungrouped)", True),
    ("(ungrouped)", "", True),
    ("(ungrouped)", "(ungrouped)", True),
    ("mygroup", "mygroup", True),
    ("mygroup", "", False),
    ("mygroup", "other", False),
    ("", "mygroup", False),
    ("  ", "  ", True),          # whitespace stripped → both empty
    ("grp", "GRP", False),       # case-sensitive for named groups
])
def test_group_matches(agent, proc_group, target_group, expected):
    assert _group_matches(agent, proc_group, target_group) == expected
