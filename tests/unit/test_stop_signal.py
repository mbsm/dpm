"""Tests for configurable stop signal."""

import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CONFIG_PATH = Path(__file__).parent.parent.parent / "dpm.yaml"


@pytest.fixture
def agent_with_sigint():
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
    """Default agent (from dpm.yaml with stop_signal key) uses SIGINT."""
    assert agent.stop_signal == signal.SIGINT


def test_invalid_stop_signal_falls_back():
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
