"""Tests for the max_restarts circuit breaker."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dpmd.processes import create_process, monitor_process, start_process

CONFIG_PATH = Path(__file__).parent.parent.parent / "dpm.yaml"


@pytest.fixture
def agent_with_max_restarts(config_path):
    """Daemon with max_restarts=3 and mocked LCM."""
    with patch("dpmd.daemon.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        with patch("dpmd.daemon.Daemon.load_config") as mock_config:
            mock_config.return_value = {
                "command_channel": "DPM/commands",
                "host_info_channel": "DPM/host_info",
                "log_chunks_channel": "DPM/log_chunks",
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
            from dpmd.daemon import Daemon
            a = Daemon(config_file=str(CONFIG_PATH))
            yield a


def test_suspended_after_max_restarts(agent_with_max_restarts):
    """Process transitions to SUSPENDED after max_restarts failures."""
    from dpm.constants import STATE_FAILED, STATE_SUSPENDED
    agent = agent_with_max_restarts
    create_process(agent, "test", "false", True, False, "grp")

    # Simulate a running process that has exited — state must be RUNNING
    # so monitor_process proceeds past the early-return guard.
    from dpm.constants import STATE_RUNNING
    agent.processes["test"].state = STATE_RUNNING
    agent.processes["test"].auto_restart = True
    agent.processes["test"].restart_count = 3
    agent.processes["test"].last_restart_time = 0.0
    agent.processes["test"].exit_code = 1

    # Mock the proc as not running (exited)
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"].proc = mock_proc

    monitor_process(agent, "test")
    assert agent.processes["test"].state == STATE_SUSPENDED


def test_restart_below_max_not_suspended(agent_with_max_restarts):
    """Process restarts normally when below max_restarts."""
    from dpm.constants import STATE_RUNNING, STATE_SUSPENDED
    agent = agent_with_max_restarts
    create_process(agent, "test", "false", True, False, "grp")

    agent.processes["test"].state = STATE_RUNNING
    agent.processes["test"].auto_restart = True
    agent.processes["test"].restart_count = 1  # below max of 3
    agent.processes["test"].last_restart_time = 0.0
    agent.processes["test"].exit_code = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"].proc = mock_proc

    with patch("dpmd.processes.start_process") as mock_start:
        monitor_process(agent, "test")
        mock_start.assert_called_once_with(agent, "test")

    assert agent.processes["test"].state != STATE_SUSPENDED


def test_manual_start_clears_suspended(agent_with_max_restarts):
    """Manual start on a SUSPENDED process resets the restart counter."""
    from dpm.constants import STATE_SUSPENDED
    agent = agent_with_max_restarts
    create_process(agent, "test", "echo hi", True, False, "grp")

    agent.processes["test"].state = STATE_SUSPENDED
    agent.processes["test"].restart_count = 10
    agent.processes["test"].last_restart_time = 99999.0

    with patch("dpmd.processes.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        start_process(agent, "test")

    assert agent.processes["test"].restart_count == 0
    assert agent.processes["test"].last_restart_time == 0.0


def test_unlimited_restarts_when_minus_one(agent):
    """When max_restarts is -1 (default), never suspend."""
    from dpm.constants import STATE_RUNNING, STATE_SUSPENDED
    create_process(agent, "test", "false", True, False, "grp")

    agent.processes["test"].state = STATE_RUNNING
    agent.processes["test"].auto_restart = True
    agent.processes["test"].restart_count = 9999
    agent.processes["test"].last_restart_time = 0.0
    agent.processes["test"].exit_code = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"].proc = mock_proc

    with patch("dpmd.processes.start_process"):
        monitor_process(agent, "test")

    assert agent.processes["test"].state != STATE_SUSPENDED
