"""Tests for Agent config loading and validation."""

from unittest.mock import patch

import pytest
import yaml


def make_valid_config(**overrides):
    cfg = {
        "lcm_url": "udpm://239.255.76.68:7667?ttl=1",
        "command_channel": "DPM/commands",
        "host_info_channel": "DPM/host_info",
        "proc_outputs_channel": "DPM/proc_outputs",
        "host_procs_channel": "DPM/host_procs",
        "stop_timeout": 2,
        "monitor_interval": 1,
        "output_interval": 1,
        "host_status_interval": 1,
        "procs_status_interval": 1,
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture
def valid_config_file(tmp_path):
    path = tmp_path / "dpm.yaml"
    path.write_text(yaml.dump(make_valid_config()))
    return str(path)


def _make_agent(path):
    with patch("dpm.agent.agent.lcm.LCM"):
        from dpm.agent.agent import Agent
        return Agent(config_file=path)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_config_loads_all_required_keys(valid_config_file):
    agent = _make_agent(valid_config_file)
    required = [
        "command_channel", "host_info_channel", "proc_outputs_channel",
        "host_procs_channel", "stop_timeout", "monitor_interval",
        "output_interval", "host_status_interval", "procs_status_interval",
        "lcm_url",
    ]
    for key in required:
        assert key in agent.config, f"Missing key: {key}"


def test_valid_config_sets_channel_attributes(valid_config_file):
    agent = _make_agent(valid_config_file)
    assert agent.command_channel == "DPM/commands"
    assert agent.host_info_channel == "DPM/host_info"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        _make_agent(str(tmp_path / "no_such.yaml"))


def test_bad_yaml_raises_value_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(": : : {{{{")
    with pytest.raises(ValueError):
        _make_agent(str(path))


@pytest.mark.parametrize("missing_key", [
    "command_channel",
    "host_info_channel",
    "proc_outputs_channel",
    "host_procs_channel",
    "lcm_url",
    "stop_timeout",
    "monitor_interval",
])
def test_missing_required_key_raises_key_error(tmp_path, missing_key):
    cfg = make_valid_config()
    del cfg[missing_key]
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    with pytest.raises(KeyError):
        _make_agent(str(path))
