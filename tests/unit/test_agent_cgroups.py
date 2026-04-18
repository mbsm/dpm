"""Tests for cgroup integration in agent start/stop."""

from unittest.mock import MagicMock, patch

import pytest


def test_start_process_calls_setup_cgroup(agent):
    """start_process calls setup_cgroup when limits are set."""
    agent.create_process("test", "echo hi", False, False, "grp",
                         cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824)

    with patch("dpmd.daemon.psutil.Popen") as mock_popen, \
         patch("dpmd.daemon.cgroups_available", return_value=True), \
         patch("dpmd.daemon.setup_cgroup") as mock_setup:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

        mock_setup.assert_called_once_with("test", 123, cpuset="0,1",
                                           cpu_limit=1.5, mem_limit=1073741824,
                                           isolated=False)


def test_start_process_skips_cgroup_when_no_limits(agent):
    """start_process doesn't call setup_cgroup when no limits are set."""
    agent.create_process("test", "echo hi", False, False, "grp")

    with patch("dpmd.daemon.psutil.Popen") as mock_popen, \
         patch("dpmd.daemon.setup_cgroup") as mock_setup:
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

    with patch("dpmd.daemon.psutil.Popen") as mock_popen, \
         patch("dpmd.daemon.cgroups_available", return_value=True), \
         patch("dpmd.daemon.setup_cgroup", side_effect=OSError("permission denied")):
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

        assert agent.processes["test"].state == STATE_RUNNING


def test_stop_process_calls_cleanup_cgroup(agent):
    """stop_process calls cleanup_cgroup."""
    agent.create_process("test", "echo hi", False, False, "grp",
                         cpuset="0,1")

    mock_proc = MagicMock()
    mock_proc.pid = 123
    mock_proc.poll.return_value = None
    mock_proc.returncode = 0
    agent.processes["test"].proc = mock_proc
    agent.processes["test"].state = "R"

    with patch("dpmd.daemon.cleanup_cgroup") as mock_cleanup, \
         patch.object(agent, "_kill_process_group", return_value=True):
        agent.stop_process("test")
        mock_cleanup.assert_called_once_with("test")
