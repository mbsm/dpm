"""Tests for Daemon.command_handler: routing and hostname filtering."""

from unittest.mock import patch

import pytest

from dpm_msgs import command_t


def _cmd(action, name="p1", group="grp", hostname="", exec_cmd="echo hi",
         auto_restart=False, realtime=False):
    msg = command_t()
    msg.action = action
    msg.name = name
    msg.group = group
    msg.hostname = hostname
    msg.exec_command = exec_cmd
    msg.auto_restart = auto_restart
    msg.realtime = realtime
    return msg.encode()


# ---------------------------------------------------------------------------
# Hostname filtering (fix #1)
# ---------------------------------------------------------------------------

def test_command_for_this_host_is_dispatched(agent):
    with patch.object(agent, "start_process") as mock:
        agent.command_handler("ch", _cmd("start_process", hostname=agent.hostname))
    mock.assert_called_once_with("p1")


def test_command_for_other_host_is_ignored(agent):
    with patch.object(agent, "start_process") as mock:
        agent.command_handler("ch", _cmd("start_process", hostname="other-host-xyz"))
    mock.assert_not_called()


def test_empty_hostname_broadcast_is_dispatched(agent):
    """An empty hostname must reach every node (broadcast semantics)."""
    with patch.object(agent, "start_process") as mock:
        agent.command_handler("ch", _cmd("start_process", hostname=""))
    mock.assert_called_once_with("p1")


# ---------------------------------------------------------------------------
# Action routing
# ---------------------------------------------------------------------------

def test_routes_create_process(agent):
    with patch.object(agent, "create_process") as mock:
        agent.command_handler(
            "ch",
            _cmd("create_process", hostname=agent.hostname,
                 exec_cmd="sleep 1", auto_restart=True, realtime=False),
        )
    mock.assert_called_once_with("p1", "sleep 1", True, False, "grp",
                                    work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
                                    isolated=False)


def test_routes_start_process(agent):
    with patch.object(agent, "start_process") as mock:
        agent.command_handler("ch", _cmd("start_process", hostname=agent.hostname))
    mock.assert_called_once_with("p1")


def test_routes_stop_process(agent):
    with patch.object(agent, "stop_process") as mock:
        agent.command_handler("ch", _cmd("stop_process", hostname=agent.hostname))
    mock.assert_called_once_with("p1")


def test_routes_delete_process(agent):
    with patch.object(agent, "delete_process") as mock:
        agent.command_handler("ch", _cmd("delete_process", hostname=agent.hostname))
    mock.assert_called_once_with("p1")


def test_routes_start_group(agent):
    with patch.object(agent, "start_group") as mock:
        agent.command_handler(
            "ch", _cmd("start_group", group="mygrp", hostname=agent.hostname)
        )
    mock.assert_called_once_with("mygrp")


def test_routes_stop_group(agent):
    with patch.object(agent, "stop_group") as mock:
        agent.command_handler(
            "ch", _cmd("stop_group", group="mygrp", hostname=agent.hostname)
        )
    mock.assert_called_once_with("mygrp")


def test_unknown_action_does_not_raise(agent):
    """An unrecognised action should log a warning and not crash."""
    agent.command_handler("ch", _cmd("fly_to_the_moon", hostname=agent.hostname))
