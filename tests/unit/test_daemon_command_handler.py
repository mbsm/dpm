"""Tests for command_handler: routing and hostname filtering."""

from unittest.mock import patch

import pytest

from dpm.constants import DPM_PROTOCOL_VERSION
from dpm_msgs import command_t
from dpmd.commands import command_handler


def _cmd(action, name="p1", group="grp", hostname="", exec_cmd="echo hi",
         auto_restart=False, realtime=False, rt_priority=0):
    msg = command_t()
    msg.protocol_version = DPM_PROTOCOL_VERSION
    msg.action = action
    msg.name = name
    msg.group = group
    msg.hostname = hostname
    msg.exec_command = exec_cmd
    msg.auto_restart = auto_restart
    msg.realtime = realtime
    msg.rt_priority = rt_priority
    return msg.encode()


# ---------------------------------------------------------------------------
# Hostname filtering (fix #1)
# ---------------------------------------------------------------------------

def test_command_for_this_host_is_dispatched(agent):
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd("start_process", hostname=agent.hostname))
    mock.assert_called_once_with(agent, "p1")


def test_command_for_other_host_is_ignored(agent):
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd("start_process", hostname="other-host-xyz"))
    mock.assert_not_called()


def test_empty_hostname_broadcast_is_dispatched(agent):
    """An empty hostname must reach every node (broadcast semantics)."""
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd("start_process", hostname=""))
    mock.assert_called_once_with(agent, "p1")


# ---------------------------------------------------------------------------
# Action routing
# ---------------------------------------------------------------------------

def test_routes_create_process(agent):
    with patch("dpmd.commands.create_process") as mock:
        command_handler(
            agent,
            "ch",
            _cmd("create_process", hostname=agent.hostname,
                 exec_cmd="sleep 1", auto_restart=True, realtime=False),
        )
    mock.assert_called_once_with(agent, "p1", "sleep 1", True, False, "grp",
                                 work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
                                 isolated=False, rt_priority=0)


def test_routes_create_process_with_rt_priority(agent):
    """rt_priority on the command_t is plumbed through to create_process."""
    with patch("dpmd.commands.create_process") as mock:
        command_handler(
            agent,
            "ch",
            _cmd("create_process", hostname=agent.hostname,
                 exec_cmd="planner", auto_restart=False, realtime=True,
                 rt_priority=80),
        )
    _, kwargs = mock.call_args
    assert kwargs["rt_priority"] == 80


def test_create_process_clamps_out_of_range_rt_priority(agent, caplog):
    """rt_priority outside [1,99] is logged and clamped to 0 (use daemon default)."""
    from dpmd.processes import create_process
    create_process(agent, "p1", "cmd", False, True, "grp", rt_priority=120)
    assert agent.processes["p1"].rt_priority == 0
    assert any("out of range" in r.message for r in caplog.records)


def test_routes_start_process(agent):
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd("start_process", hostname=agent.hostname))
    mock.assert_called_once_with(agent, "p1")


def test_routes_stop_process(agent):
    with patch("dpmd.commands.stop_process") as mock:
        command_handler(agent, "ch", _cmd("stop_process", hostname=agent.hostname))
    mock.assert_called_once_with(agent, "p1")


def test_routes_delete_process(agent):
    with patch("dpmd.commands.delete_process") as mock:
        command_handler(agent, "ch", _cmd("delete_process", hostname=agent.hostname))
    mock.assert_called_once_with(agent, "p1")


def test_routes_start_group(agent):
    with patch("dpmd.commands.start_group") as mock:
        command_handler(
            agent, "ch", _cmd("start_group", group="mygrp", hostname=agent.hostname)
        )
    mock.assert_called_once_with(agent, "mygrp")


def test_routes_stop_group(agent):
    with patch("dpmd.commands.stop_group") as mock:
        command_handler(
            agent, "ch", _cmd("stop_group", group="mygrp", hostname=agent.hostname)
        )
    mock.assert_called_once_with(agent, "mygrp")


def test_unknown_action_does_not_raise(agent):
    """An unrecognised action should log a warning and not crash."""
    command_handler(agent, "ch", _cmd("fly_to_the_moon", hostname=agent.hostname))


# ---------------------------------------------------------------------------
# Seq-based dedup (fix #1: client-restart tolerance)
# ---------------------------------------------------------------------------

def _cmd_with_seq(action, seq, hostname="", name="p1"):
    msg = command_t()
    msg.protocol_version = DPM_PROTOCOL_VERSION
    msg.action = action
    msg.name = name
    msg.hostname = hostname
    msg.group = ""
    msg.exec_command = ""
    msg.auto_restart = False
    msg.realtime = False
    msg.seq = seq
    return msg.encode()


def test_duplicate_seq_is_dropped(agent):
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd_with_seq("start_process", 100, agent.hostname))
        command_handler(agent, "ch", _cmd_with_seq("start_process", 100, agent.hostname))
    assert mock.call_count == 1


def test_older_seq_is_dropped(agent):
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd_with_seq("start_process", 500, agent.hostname))
        command_handler(agent, "ch", _cmd_with_seq("start_process", 499, agent.hostname))
    assert mock.call_count == 1


def test_command_with_mismatched_protocol_version_is_dropped(agent):
    """A command with the wrong protocol_version must not reach the handler."""
    msg = command_t()
    msg.protocol_version = 9999  # not equal to DPM_PROTOCOL_VERSION
    msg.action = "start_process"
    msg.name = "p1"
    msg.hostname = agent.hostname
    msg.group = ""
    msg.exec_command = ""
    msg.auto_restart = False
    msg.realtime = False
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", msg.encode())
    mock.assert_not_called()


def test_large_seq_rollback_is_accepted_as_client_restart(agent):
    """Simulate a client restart after correcting a clock that was ahead."""
    from dpmd.commands import _SEQ_RESTART_THRESHOLD_USEC
    high_seq = 10_000_000_000_000  # seq from client with skewed future clock
    low_seq = high_seq - _SEQ_RESTART_THRESHOLD_USEC - 1  # > threshold below
    with patch("dpmd.commands.start_process") as mock:
        command_handler(agent, "ch", _cmd_with_seq("start_process", high_seq, agent.hostname))
        command_handler(agent, "ch", _cmd_with_seq("start_process", low_seq, agent.hostname))
    assert mock.call_count == 2  # second was accepted as restart, not dropped
