"""Tests for the max_restarts circuit breaker."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CONFIG_PATH = Path(__file__).parent.parent.parent / "dpm.yaml"


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
    agent.processes["test"].output_lock = MagicMock()
    agent.processes["test"].stdout_lines = []
    agent.processes["test"].stderr_lines = []

    agent.monitor_process("test")
    assert agent.processes["test"].state == STATE_SUSPENDED


def test_restart_below_max_not_suspended(agent_with_max_restarts):
    """Process restarts normally when below max_restarts."""
    from dpm.constants import STATE_RUNNING, STATE_SUSPENDED
    agent = agent_with_max_restarts
    agent.create_process("test", "false", True, False, "grp")

    agent.processes["test"].state = STATE_RUNNING
    agent.processes["test"].auto_restart = True
    agent.processes["test"].restart_count = 1  # below max of 3
    agent.processes["test"].last_restart_time = 0.0
    agent.processes["test"].exit_code = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"].proc = mock_proc
    agent.processes["test"].output_lock = MagicMock()
    agent.processes["test"].stdout_lines = []
    agent.processes["test"].stderr_lines = []

    with patch.object(agent, "start_process") as mock_start:
        agent.monitor_process("test")
        mock_start.assert_called_once_with("test")

    assert agent.processes["test"].state != STATE_SUSPENDED


def test_manual_start_clears_suspended(agent_with_max_restarts):
    """Manual start on a SUSPENDED process resets the restart counter."""
    from dpm.constants import STATE_SUSPENDED
    agent = agent_with_max_restarts
    agent.create_process("test", "echo hi", True, False, "grp")

    agent.processes["test"].state = STATE_SUSPENDED
    agent.processes["test"].restart_count = 10
    agent.processes["test"].last_restart_time = 99999.0

    with patch("dpm.agent.agent.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent.start_process("test")

    assert agent.processes["test"].restart_count == 0
    assert agent.processes["test"].last_restart_time == 0.0


def test_unlimited_restarts_when_minus_one(agent):
    """When max_restarts is -1 (default), never suspend."""
    from dpm.constants import STATE_RUNNING, STATE_SUSPENDED
    agent.create_process("test", "false", True, False, "grp")

    agent.processes["test"].state = STATE_RUNNING
    agent.processes["test"].auto_restart = True
    agent.processes["test"].restart_count = 9999
    agent.processes["test"].last_restart_time = 0.0
    agent.processes["test"].exit_code = 1

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    agent.processes["test"].proc = mock_proc
    agent.processes["test"].output_lock = MagicMock()
    agent.processes["test"].stdout_lines = []
    agent.processes["test"].stderr_lines = []

    with patch.object(agent, "start_process"):
        agent.monitor_process("test")

    assert agent.processes["test"].state != STATE_SUSPENDED
