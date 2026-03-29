"""Tests for Controller config loading and validation."""

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
    }
    cfg.update(overrides)
    return cfg


def _make_controller(path):
    with patch("dpm.controller.controller.lcm.LCM"):
        from dpm.controller.controller import Controller
        return Controller(path)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_config_loads_all_required_keys(config_path):
    ctrl = _make_controller(config_path)
    for key in ["command_channel", "host_info_channel", "proc_outputs_channel",
                "host_procs_channel", "lcm_url"]:
        assert key in ctrl.config


def test_valid_config_sets_attributes(tmp_path):
    cfg = make_valid_config(command_channel="MY/cmd")
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    ctrl = _make_controller(str(path))
    assert ctrl.command_channel == "MY/cmd"
    assert ctrl.lc_url == "udpm://239.255.76.68:7667?ttl=1"


def test_initial_state_is_empty(config_path):
    ctrl = _make_controller(config_path)
    assert ctrl.hosts == {}
    assert ctrl.procs == {}
    assert ctrl._running is False
    assert ctrl._thread is None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _make_controller(str(tmp_path / "no.yaml"))


def test_bad_yaml_raises_value_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(": : {{")
    with pytest.raises(ValueError):
        _make_controller(str(path))


@pytest.mark.parametrize("missing_key", [
    "command_channel",
    "host_info_channel",
    "proc_outputs_channel",
    "host_procs_channel",
    "lcm_url",
])
def test_missing_required_key_raises_key_error(tmp_path, missing_key):
    cfg = make_valid_config()
    del cfg[missing_key]
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    with pytest.raises(KeyError):
        _make_controller(str(path))
