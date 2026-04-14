"""Tests for YAML launch script parsing and execution."""

from unittest.mock import MagicMock, patch, call

import pytest
import yaml


def _write_script(tmp_path, steps):
    path = tmp_path / "launch.yaml"
    data = {"name": "test", "timeout": 5, "steps": steps}
    path.write_text(yaml.safe_dump(data))
    return str(path)


def test_parse_launch_script(tmp_path):
    from dpm.cli.launch import parse_launch_script
    path = _write_script(tmp_path, [
        {"start": "foo@host1"},
        {"stop": "bar@host1"},
        {"sleep": 1.0},
        {"wait_running": {"targets": ["foo@host1"], "timeout": 10}},
    ])
    script = parse_launch_script(path)
    assert script["name"] == "test"
    assert script["timeout"] == 5
    assert len(script["steps"]) == 4


def test_parse_launch_script_missing_file():
    from dpm.cli.launch import parse_launch_script
    with pytest.raises(FileNotFoundError):
        parse_launch_script("/nonexistent/file.yaml")


def test_reverse_steps():
    from dpm.cli.launch import reverse_steps
    steps = [
        {"start": "a@h1"},
        {"start": "b@h1"},
        {"wait_running": {"targets": ["a@h1", "b@h1"]}},
        {"start": "c@h2"},
        {"sleep": 2.0},
    ]
    reversed_steps = reverse_steps(steps)
    assert reversed_steps == [
        {"sleep": 2.0},
        {"stop": "c@h2"},
        {"wait_stopped": {"targets": ["a@h1", "b@h1"]}},
        {"stop": "b@h1"},
        {"stop": "a@h1"},
    ]


def test_reverse_steps_skips_create():
    from dpm.cli.launch import reverse_steps
    steps = [
        {"create": {"name": "foo", "host": "h1", "cmd": "echo"}},
        {"start": "foo@h1"},
    ]
    reversed_steps = reverse_steps(steps)
    assert reversed_steps == [
        {"stop": "foo@h1"},
    ]


def test_execute_start_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"start": "foo@host1"}
    result = execute_step(sup, step, default_timeout=5)
    sup.start_proc.assert_called_once_with("foo", "host1")
    assert result is True


def test_execute_stop_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"stop": "bar@host1"}
    result = execute_step(sup, step, default_timeout=5)
    sup.stop_proc.assert_called_once_with("bar", "host1")
    assert result is True


def test_execute_sleep_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"sleep": 0.01}
    with patch("dpm.cli.launch.time.sleep") as mock_sleep:
        result = execute_step(sup, step, default_timeout=5)
        mock_sleep.assert_called_once_with(0.01)
    assert result is True


def test_execute_wait_running_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"wait_running": {"targets": ["foo@host1"], "timeout": 2}}

    with patch("dpm.cli.launch.wait_for_state", return_value=True) as mock_wait:
        result = execute_step(sup, step, default_timeout=5)
        mock_wait.assert_called_once_with(sup, "foo", "host1", target="R", timeout=2)
    assert result is True


def test_execute_wait_running_timeout():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"wait_running": {"targets": ["foo@host1"], "timeout": 2}}

    with patch("dpm.cli.launch.wait_for_state", return_value=False):
        result = execute_step(sup, step, default_timeout=5)
    assert result is False


def test_execute_create_step():
    from dpm.cli.launch import execute_step
    sup = MagicMock()
    step = {"create": {
        "name": "foo", "host": "h1", "cmd": "echo hi",
        "group": "grp", "auto_restart": True,
    }}
    result = execute_step(sup, step, default_timeout=5)
    sup.create_proc.assert_called_once_with(
        "foo", "echo hi", "grp", "h1", True, False,
        work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
    )
    assert result is True
