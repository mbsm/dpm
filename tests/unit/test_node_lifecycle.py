"""Tests for NodeAgent process lifecycle state machine (no real subprocesses)."""

import pytest

from dpm.node.node import STATE_FAILED, STATE_READY, STATE_RUNNING


# ---------------------------------------------------------------------------
# create_process
# ---------------------------------------------------------------------------

def test_create_sets_initial_state(node_agent):
    node_agent.create_process("p1", "echo hi", False, False, "grp")
    p = node_agent.processes["p1"]
    assert p["state"] == STATE_READY
    assert p["status"] == "stopped"
    assert p["proc"] is None
    assert p["exit_code"] == -1
    assert p["stdout"] == ""
    assert p["stderr"] == ""
    assert p["errors"] == ""


def test_create_stores_metadata(node_agent):
    node_agent.create_process("p1", "sleep 100", True, True, "mygroup")
    p = node_agent.processes["p1"]
    assert p["exec_command"] == "sleep 100"
    assert p["auto_restart"] is True
    assert p["realtime"] is True
    assert p["group"] == "mygroup"


def test_create_initialises_output_lists(node_agent):
    node_agent.create_process("p1", "cmd", False, False, "")
    p = node_agent.processes["p1"]
    assert isinstance(p["stdout_lines"], list)
    assert isinstance(p["stderr_lines"], list)
    assert p["stdout_lines"] == []
    assert p["stderr_lines"] == []


def test_create_overwrites_existing_entry(node_agent):
    node_agent.create_process("p1", "echo a", False, False, "g1")
    node_agent.create_process("p1", "echo b", True, False, "g2")
    p = node_agent.processes["p1"]
    assert p["exec_command"] == "echo b"
    assert p["auto_restart"] is True
    assert p["group"] == "g2"


# ---------------------------------------------------------------------------
# start_process — guard paths (no real subprocess)
# ---------------------------------------------------------------------------

def test_start_unknown_process_no_crash(node_agent):
    node_agent.start_process("nonexistent")  # should log warning and return cleanly


def test_start_already_running_process_no_crash(node_agent):
    """If proc.poll() returns None (still running), start should be a no-op."""
    from unittest.mock import MagicMock
    node_agent.create_process("p1", "sleep 100", False, False, "")
    # Plant a fake "running" proc
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    node_agent.processes["p1"]["proc"] = fake_proc
    node_agent.processes["p1"]["state"] = STATE_RUNNING
    node_agent.start_process("p1")
    # Still the same fake proc — no re-spawn
    assert node_agent.processes["p1"]["proc"] is fake_proc


# ---------------------------------------------------------------------------
# stop_process — guard paths
# ---------------------------------------------------------------------------

def test_stop_unknown_process_no_crash(node_agent):
    node_agent.stop_process("nonexistent")


def test_stop_process_with_no_proc_is_noop(node_agent):
    node_agent.create_process("p1", "cmd", False, False, "")
    # proc is None by default → should be a no-op
    node_agent.stop_process("p1")
    assert node_agent.processes["p1"]["state"] == STATE_READY


# ---------------------------------------------------------------------------
# delete_process — guard paths
# ---------------------------------------------------------------------------

def test_delete_unknown_process_no_crash(node_agent):
    node_agent.delete_process("nonexistent")


def test_delete_removes_from_table(node_agent):
    node_agent.create_process("p1", "cmd", False, False, "")
    node_agent.delete_process("p1")
    assert "p1" not in node_agent.processes


def test_delete_calls_stop_if_proc_running(node_agent):
    from unittest.mock import MagicMock, patch
    node_agent.create_process("p1", "sleep 10", False, False, "")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    node_agent.processes["p1"]["proc"] = fake_proc
    with patch.object(node_agent, "stop_process") as mock_stop:
        node_agent.delete_process("p1")
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
def test_group_matches(node_agent, proc_group, target_group, expected):
    assert node_agent._group_matches(proc_group, target_group) == expected
