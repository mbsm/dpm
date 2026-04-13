"""Tests for Agent process lifecycle state machine (no real subprocesses)."""

import pytest

from dpm.agent.agent import STATE_FAILED, STATE_KILLED, STATE_READY, STATE_RUNNING


# ---------------------------------------------------------------------------
# create_process
# ---------------------------------------------------------------------------

def test_create_sets_initial_state(agent):
    agent.create_process("p1", "echo hi", False, False, "grp")
    p = agent.processes["p1"]
    assert p["state"] == STATE_READY
    assert p["proc"] is None
    assert p["exit_code"] == -1
    assert p["stdout"] == ""
    assert p["stderr"] == ""
    assert p["errors"] == ""
    assert p["restart_count"] == 0


def test_create_stores_metadata(agent):
    agent.create_process("p1", "sleep 100", True, True, "mygroup")
    p = agent.processes["p1"]
    assert p["exec_command"] == "sleep 100"
    assert p["auto_restart"] is True
    assert p["realtime"] is True
    assert p["group"] == "mygroup"


def test_create_does_not_preallocate_output_locks(agent):
    """output_lock, stdout_lines, stderr_lines are created at start_process time."""
    agent.create_process("p1", "cmd", False, False, "")
    p = agent.processes["p1"]
    assert "output_lock" not in p
    assert "stdout_lines" not in p
    assert "stderr_lines" not in p


def test_create_overwrites_existing_entry(agent):
    agent.create_process("p1", "echo a", False, False, "g1")
    agent.create_process("p1", "echo b", True, False, "g2")
    p = agent.processes["p1"]
    assert p["exec_command"] == "echo b"
    assert p["auto_restart"] is True
    assert p["group"] == "g2"


def test_create_stops_running_process_before_overwrite(agent):
    from unittest.mock import MagicMock, patch
    agent.create_process("p1", "echo a", False, False, "g1")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    agent.processes["p1"]["proc"] = fake_proc
    with patch.object(agent, "stop_process") as mock_stop:
        agent.create_process("p1", "echo b", False, False, "g2")
    mock_stop.assert_called_once_with("p1")


# ---------------------------------------------------------------------------
# start_process — guard paths (no real subprocess)
# ---------------------------------------------------------------------------

def test_start_unknown_process_no_crash(agent):
    agent.start_process("nonexistent")  # should log warning and return cleanly


def test_start_already_running_process_no_crash(agent):
    """If proc.poll() returns None (still running), start should be a no-op."""
    from unittest.mock import MagicMock
    agent.create_process("p1", "sleep 100", False, False, "")
    # Plant a fake "running" proc
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    agent.processes["p1"]["proc"] = fake_proc
    agent.processes["p1"]["state"] = STATE_RUNNING
    agent.start_process("p1")
    # Still the same fake proc — no re-spawn
    assert agent.processes["p1"]["proc"] is fake_proc


# ---------------------------------------------------------------------------
# stop_process — guard paths
# ---------------------------------------------------------------------------

def test_stop_unknown_process_no_crash(agent):
    agent.stop_process("nonexistent")


def test_stop_process_with_no_proc_is_noop(agent):
    agent.create_process("p1", "cmd", False, False, "")
    # proc is None by default → should be a no-op
    agent.stop_process("p1")
    assert agent.processes["p1"]["state"] == STATE_READY


# ---------------------------------------------------------------------------
# delete_process — guard paths
# ---------------------------------------------------------------------------

def test_delete_unknown_process_no_crash(agent):
    agent.delete_process("nonexistent")


def test_delete_removes_from_table(agent):
    agent.create_process("p1", "cmd", False, False, "")
    agent.delete_process("p1")
    assert "p1" not in agent.processes


def test_delete_calls_stop_if_proc_running(agent):
    from unittest.mock import MagicMock, patch
    agent.create_process("p1", "sleep 10", False, False, "")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    agent.processes["p1"]["proc"] = fake_proc
    with patch.object(agent, "stop_process") as mock_stop:
        agent.delete_process("p1")
    mock_stop.assert_called_once_with("p1")


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
    assert agent._group_matches(proc_group, target_group) == expected
