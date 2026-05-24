"""Tests for declarative launch system (group-based dependency orchestration)."""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml


def _write_launch_file(tmp_path, data):
    path = tmp_path / "launch.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


# --- parse_launch_file ---

def test_parse_launch_file_basic(tmp_path):
    from dpm.operations import parse_launch_file
    path = _write_launch_file(tmp_path, {
        "name": "test",
        "timeout": 10,
        "groups": {
            "core": {"requires": []},
            "sensors": {"requires": ["core"]},
        },
    })
    script = parse_launch_file(path)
    assert script["name"] == "test"
    assert script["timeout"] == 10
    assert "core" in script["groups"]
    assert "sensors" in script["groups"]
    assert script["groups"]["sensors"]["requires"] == ["core"]


def test_parse_launch_file_with_processes(tmp_path):
    from dpm.operations import parse_launch_file
    path = _write_launch_file(tmp_path, {
        "name": "full",
        "timeout": 5,
        "groups": {"core": None},
        "processes": [
            {"name": "svc", "host": "h1", "cmd": "echo hi", "group": "core"},
        ],
    })
    script = parse_launch_file(path)
    assert len(script["processes"]) == 1
    assert script["processes"][0]["name"] == "svc"


def test_parse_launch_file_missing_file():
    from dpm.operations import parse_launch_file
    with pytest.raises(FileNotFoundError):
        parse_launch_file("/nonexistent/file.yaml")


def test_parse_launch_file_not_dict(tmp_path):
    from dpm.operations import parse_launch_file
    path = _write_launch_file(tmp_path, ["not", "a", "dict"])
    with pytest.raises(ValueError, match="YAML dict"):
        parse_launch_file(path)


def test_parse_launch_file_string_requires(tmp_path):
    """A single string for requires should be wrapped into a list."""
    from dpm.operations import parse_launch_file
    path = _write_launch_file(tmp_path, {
        "name": "test",
        "groups": {
            "core": None,
            "sensors": {"requires": "core"},
        },
    })
    script = parse_launch_file(path)
    assert script["groups"]["sensors"]["requires"] == ["core"]


# --- graph validation (via resolve_waves) ---

def test_validate_group_refs_valid():
    from dpm.operations import _validate_group_refs
    groups = {
        "core": {"requires": [], "after": []},
        "sensors": {"requires": ["core"], "after": []},
        "ui": {"requires": [], "after": ["sensors"]},
    }
    _validate_group_refs(groups)  # should not raise


def test_validate_group_refs_unknown_reference():
    from dpm.operations import _validate_group_refs
    groups = {
        "core": {"requires": ["nonexistent"], "after": []},
    }
    with pytest.raises(ValueError, match="unknown group"):
        _validate_group_refs(groups)


def test_resolve_waves_detects_cycle():
    from dpm.operations import resolve_waves
    groups = {
        "a": {"requires": ["b"], "after": []},
        "b": {"requires": ["a"], "after": []},
    }
    with pytest.raises(ValueError, match="[Cc]ycle"):
        resolve_waves(groups)


# --- resolve_waves ---

def test_resolve_waves_linear():
    from dpm.operations import resolve_waves
    groups = {
        "core": {"requires": [], "after": []},
        "sensors": {"requires": ["core"], "after": []},
        "ui": {"requires": ["sensors"], "after": []},
    }
    waves = resolve_waves(groups)
    assert len(waves) == 3
    assert waves[0] == ["core"]
    assert waves[1] == ["sensors"]
    assert waves[2] == ["ui"]


def test_resolve_waves_parallel():
    from dpm.operations import resolve_waves
    groups = {
        "core": {"requires": [], "after": []},
        "sensors": {"requires": [], "after": []},
        "ui": {"requires": ["core", "sensors"], "after": []},
    }
    waves = resolve_waves(groups)
    assert len(waves) == 2
    # core and sensors are independent — both in wave 1 (sorted alphabetically)
    assert waves[0] == ["core", "sensors"]
    assert waves[1] == ["ui"]


def test_resolve_waves_single_group():
    from dpm.operations import resolve_waves
    groups = {
        "core": {"requires": [], "after": []},
    }
    waves = resolve_waves(groups)
    assert waves == [["core"]]


def test_resolve_waves_after_dependency():
    """'after' should order groups but not be a hard requirement."""
    from dpm.operations import resolve_waves
    groups = {
        "core": {"requires": [], "after": []},
        "logging": {"requires": [], "after": ["core"]},
    }
    waves = resolve_waves(groups)
    assert len(waves) == 2
    assert waves[0] == ["core"]
    assert waves[1] == ["logging"]


# --- _create_processes_from_script ---

def test_create_processes_success():
    from dpm.operations import _create_processes_from_script
    sup = MagicMock()
    procs = [
        {"name": "svc", "cmd": "echo hi", "host": "h1", "group": "core"},
    ]
    errors, sent = _create_processes_from_script(sup, procs, MagicMock())
    assert errors == 0
    assert sent == [("h1", "svc")]
    sup.create_proc.assert_called_once_with(
        "svc", "echo hi", "core", "h1", False, False,
        rt_priority=0,
        work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0, isolated=False,
    )


def test_create_processes_with_options():
    from dpm.operations import _create_processes_from_script
    sup = MagicMock()
    procs = [
        {
            "name": "svc", "cmd": "echo hi", "host": "h1", "group": "core",
            "auto_restart": True, "realtime": True, "isolated": True,
            "work_dir": "/tmp", "cpuset": "0,1", "cpu_limit": 1.5, "mem_limit": 1024,
            "rt_priority": 70,
        },
    ]
    errors, sent = _create_processes_from_script(sup, procs, MagicMock())
    assert errors == 0
    assert sent == [("h1", "svc")]
    sup.create_proc.assert_called_once_with(
        "svc", "echo hi", "core", "h1", True, True,
        rt_priority=70,
        work_dir="/tmp", cpuset="0,1", cpu_limit=1.5, mem_limit=1024, isolated=True,
    )


def test_create_processes_error_counted():
    from dpm.operations import _create_processes_from_script
    sup = MagicMock()
    sup.create_proc.side_effect = RuntimeError("fail")
    procs = [
        {"name": "svc", "cmd": "echo hi", "host": "h1", "group": "core"},
    ]
    errors, sent = _create_processes_from_script(sup, procs, MagicMock())
    assert errors == 1
    assert sent == []


# --- _fan_out_group ---

def _mock_client_with_procs(procs_dict):
    """Create a mock client whose .procs property returns procs_dict."""
    sup = MagicMock()
    type(sup).procs = PropertyMock(return_value=procs_dict)
    return sup


def test_fan_out_group_start():
    from dpm.operations import _fan_out_group
    proc = MagicMock()
    proc.group = "core"
    sup = _mock_client_with_procs({("h1", "svc"): proc})
    result = _fan_out_group(sup, "core", sup.start_group)
    assert result == [("h1", "svc")]
    sup.start_group.assert_called_once_with("core", "h1")


def test_fan_out_group_stop():
    from dpm.operations import _fan_out_group
    proc = MagicMock()
    proc.group = "core"
    sup = _mock_client_with_procs({("h1", "svc"): proc})
    result = _fan_out_group(sup, "core", sup.stop_group)
    assert result == [("h1", "svc")]
    sup.stop_group.assert_called_once_with("core", "h1")


def test_fan_out_group_empty():
    from dpm.operations import _fan_out_group
    sup = _mock_client_with_procs({})
    result = _fan_out_group(sup, "nonexistent", sup.start_group)
    assert result == []
    sup.start_group.assert_not_called()


# --- _wait_group ---

def test_wait_group_running_success():
    from dpm.operations import _wait_group
    proc = MagicMock()
    proc.group = "core"
    sup = _mock_client_with_procs({("h1", "svc"): proc})
    with patch("dpm.operations.wait_for_state", return_value=True):
        ok, failed = _wait_group(sup, "core", timeout=5, running=True)
    assert ok is True
    assert failed == []


def test_wait_group_running_timeout():
    from dpm.operations import _wait_group
    proc = MagicMock()
    proc.group = "core"
    proc.state = "F"
    proc.errors = "binary not found"
    sup = _mock_client_with_procs({("h1", "svc"): proc})
    with patch("dpm.operations.wait_for_state", return_value=False):
        ok, failed = _wait_group(sup, "core", timeout=5, running=True)
    assert ok is False
    assert len(failed) == 1
    label = failed[0]
    assert label.startswith("svc@h1")
    assert "state=F" in label
    assert "binary not found" in label


def test_wait_group_stopped_success():
    from dpm.operations import _wait_group
    proc = MagicMock()
    proc.group = "core"
    sup = _mock_client_with_procs({("h1", "svc"): proc})
    with patch("dpm.operations.wait_for_state", return_value=True):
        ok, failed = _wait_group(sup, "core", timeout=5, running=False)
    assert ok is True
    assert failed == []


# --- check_launch_file ---

def test_check_clean_file(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "name": "ok",
        "timeout": 10,
        "groups": {"core": {}, "sensors": {"requires": ["core"]}},
        "processes": [
            {"name": "svc", "host": "h1", "cmd": "/bin/echo", "group": "core"},
            {"name": "lidar", "host": "h1", "cmd": "/bin/echo", "group": "sensors"},
        ],
    })
    errors, warnings = check_launch_file(path)
    assert errors == []
    assert warnings == []


def test_check_detects_requires_typo(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "groups": {"core": {}, "sensors": {"requieres": ["core"]}},
    })
    _, warnings = check_launch_file(path)
    assert any("requieres" in w and "requires" in w for w in warnings)


def test_check_detects_unknown_group_ref(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "groups": {"core": {"requires": ["base"]}},
    })
    errors, _ = check_launch_file(path)
    assert any("unknown group 'base'" in e for e in errors)


def test_check_detects_cycle(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "groups": {"a": {"requires": ["b"]}, "b": {"requires": ["a"]}},
    })
    errors, _ = check_launch_file(path)
    assert any("cycle" in e.lower() for e in errors)


def test_check_missing_required_process_fields(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "processes": [{"name": "svc"}],
    })
    errors, _ = check_launch_file(path)
    assert any("missing required field 'cmd'" in e for e in errors)
    assert any("missing required field 'host'" in e for e in errors)


def test_check_duplicate_process(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "processes": [
            {"name": "svc", "host": "h1", "cmd": "/bin/echo"},
            {"name": "svc", "host": "h1", "cmd": "/bin/echo"},
        ],
    })
    errors, _ = check_launch_file(path)
    assert any("Duplicate process" in e for e in errors)


def test_check_process_references_unknown_group(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "groups": {"core": {}},
        "processes": [
            {"name": "svc", "host": "h1", "cmd": "/bin/echo", "group": "missing"},
        ],
    })
    _, warnings = check_launch_file(path)
    assert any("not defined under 'groups:'" in w for w in warnings)


def test_check_relative_cmd_path_warns(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {
        "processes": [{"name": "svc", "host": "h1", "cmd": "bin/foo --flag"}],
    })
    _, warnings = check_launch_file(path)
    assert any("relative path" in w for w in warnings)


def test_check_unknown_top_level_key(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {"groupz": {}})
    _, warnings = check_launch_file(path)
    assert any("groupz" in w and "groups" in w for w in warnings)


def test_check_file_not_found():
    from dpm.operations import check_launch_file
    errors, warnings = check_launch_file("/nonexistent/launch.yaml")
    assert any("not found" in e.lower() for e in errors)
    assert warnings == []


def test_check_yaml_parse_error(tmp_path):
    from dpm.operations import check_launch_file
    path = tmp_path / "bad.yaml"
    path.write_text("name: [unterminated\n")
    errors, _ = check_launch_file(str(path))
    assert any("parse error" in e.lower() for e in errors)


def test_check_top_level_not_mapping(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, ["a", "b"])
    errors, _ = check_launch_file(path)
    assert any("must be a YAML mapping" in e for e in errors)


def test_check_bad_timeout(tmp_path):
    from dpm.operations import check_launch_file
    path = _write_launch_file(tmp_path, {"timeout": "soon", "groups": {}})
    errors, _ = check_launch_file(path)
    assert any("'timeout' must be a number" in e for e in errors)
