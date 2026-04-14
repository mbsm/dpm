"""Tests for spec_io: YAML-based process spec save/load."""

import types
from unittest.mock import MagicMock, PropertyMock

import pytest
import yaml

from dpm.spec_io import (
    load_and_create,
    load_process_specs,
    save_all_process_specs,
    save_process_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(name="p1", host="h1", cmd="echo hi", group="", auto=False, rt=False):
    return {"name": name, "host": host, "exec_command": cmd,
            "group": group, "auto_restart": auto, "realtime": rt}


def _make_proc_ns(name="p1", hostname="h1", **kwargs):
    """SimpleNamespace that looks like a proc_info_t for save_all tests."""
    defaults = dict(name=name, hostname=hostname, exec_command="echo hi",
                    group="", auto_restart=False, realtime=False)
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


class MockSupervisor:
    def __init__(self, proc_list=None, create_raises=None):
        self._procs = {p.name: p for p in (proc_list or [])}
        self._created = []
        self._create_raises = create_raises

    def create_proc(self, name, exec_command, group, host, auto_restart, realtime, **kwargs):
        if self._create_raises:
            raise self._create_raises
        self._created.append(name)

    @property
    def procs(self):
        return dict(self._procs)


# ---------------------------------------------------------------------------
# save_process_spec
# ---------------------------------------------------------------------------

def test_save_single_spec_creates_file(tmp_path):
    path = str(tmp_path / "specs.yaml")
    spec = _make_spec()
    save_process_spec(path, spec)
    data = yaml.safe_load(open(path))
    assert data["name"] == "p1"


def test_save_append_to_missing_file_creates_list(tmp_path):
    path = str(tmp_path / "specs.yaml")
    spec = _make_spec()
    save_process_spec(path, spec, append=True)
    data = yaml.safe_load(open(path))
    # No existing file → single spec written
    assert data["name"] == "p1"


def test_save_append_to_existing_list(tmp_path):
    path = str(tmp_path / "specs.yaml")
    spec_a = _make_spec("a")
    spec_b = _make_spec("b")
    save_process_spec(path, spec_a)  # creates list? No — writes single dict
    # Write a list first so append has a list to extend
    with open(path, "w") as f:
        yaml.safe_dump([spec_a], f)
    save_process_spec(path, spec_b, append=True)
    data = yaml.safe_load(open(path))
    assert isinstance(data, list)
    names = [d["name"] for d in data]
    assert "a" in names and "b" in names


def test_save_append_to_existing_dict_converts_to_list(tmp_path):
    path = str(tmp_path / "specs.yaml")
    spec_a = _make_spec("a")
    spec_b = _make_spec("b")
    save_process_spec(path, spec_a)  # writes single dict
    save_process_spec(path, spec_b, append=True)
    data = yaml.safe_load(open(path))
    assert isinstance(data, list)
    assert len(data) == 2


def test_save_creates_parent_directories(tmp_path):
    path = str(tmp_path / "deep" / "dir" / "specs.yaml")
    save_process_spec(path, _make_spec())
    assert (tmp_path / "deep" / "dir" / "specs.yaml").exists()


# ---------------------------------------------------------------------------
# load_process_specs
# ---------------------------------------------------------------------------

def test_load_single_dict_wrapped_in_list(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.dump(_make_spec()))
    result = load_process_specs(str(path))
    assert isinstance(result, list)
    assert result[0]["name"] == "p1"


def test_load_list_of_dicts(tmp_path):
    path = tmp_path / "s.yaml"
    specs = [_make_spec("a"), _make_spec("b")]
    path.write_text(yaml.dump(specs))
    result = load_process_specs(str(path))
    assert len(result) == 2
    assert {r["name"] for r in result} == {"a", "b"}


def test_load_empty_file_returns_empty_list(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("")
    result = load_process_specs(str(path))
    assert result == []


def test_load_list_skips_non_dict_entries(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.dump([_make_spec("a"), "not-a-dict", 42]))
    result = load_process_specs(str(path))
    assert len(result) == 1
    assert result[0]["name"] == "a"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_process_specs(str(tmp_path / "no_such.yaml"))


def test_load_unsupported_format_raises(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.dump(12345))  # plain integer
    with pytest.raises(ValueError):
        load_process_specs(str(path))


# ---------------------------------------------------------------------------
# load_and_create
# ---------------------------------------------------------------------------

def test_load_and_create_all_valid(tmp_path):
    path = tmp_path / "s.yaml"
    specs = [_make_spec("a", "host1"), _make_spec("b", "host2")]
    path.write_text(yaml.dump(specs))
    ctrl = MockSupervisor()
    created, errors = load_and_create(str(path), ctrl)
    assert set(created) == {"a@host1", "b@host2"}
    assert errors == []


def test_load_and_create_partial_success_missing_field(tmp_path):
    path = tmp_path / "s.yaml"
    good = _make_spec("good", "h1")
    bad = {"name": "bad"}  # missing host and exec_command
    path.write_text(yaml.dump([good, bad]))
    ctrl = MockSupervisor()
    created, errors = load_and_create(str(path), ctrl)
    assert len(created) == 1
    assert "good@h1" in created
    assert len(errors) == 1
    assert errors[0][0]["name"] == "bad"


def test_load_and_create_supervisor_exception_captured(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.dump([_make_spec()]))
    ctrl = MockSupervisor(create_raises=RuntimeError("LCM down"))
    created, errors = load_and_create(str(path), ctrl)
    assert created == []
    assert len(errors) == 1
    assert "LCM down" in errors[0][1]


# ---------------------------------------------------------------------------
# save_all_process_specs
# ---------------------------------------------------------------------------

def test_save_all_writes_all_valid_procs(tmp_path):
    path = str(tmp_path / "all.yaml")
    procs = [_make_proc_ns("a", "h1"), _make_proc_ns("b", "h2")]
    ctrl = MockSupervisor(proc_list=procs)
    written, skipped = save_all_process_specs(path, ctrl)
    assert written == 2
    assert skipped == 0
    data = yaml.safe_load(open(path))
    assert isinstance(data, list)
    names = {d["name"] for d in data}
    assert names == {"a", "b"}


def test_save_all_skips_procs_missing_required_fields(tmp_path):
    path = str(tmp_path / "all.yaml")
    good = _make_proc_ns("good", "h1")
    bad = types.SimpleNamespace(name="bad", hostname="", exec_command="",
                                group="", auto_restart=False, realtime=False)
    ctrl = MockSupervisor(proc_list=[good, bad])
    written, skipped = save_all_process_specs(path, ctrl)
    assert written == 1
    assert skipped == 1


def test_save_all_append_merges_with_existing(tmp_path):
    path = str(tmp_path / "all.yaml")
    existing = [_make_spec("existing")]
    with open(path, "w") as f:
        yaml.safe_dump(existing, f)
    ctrl = MockSupervisor(proc_list=[_make_proc_ns("new", "h1")])
    save_all_process_specs(path, ctrl, append=True)
    data = yaml.safe_load(open(path))
    names = {d["name"] for d in data}
    assert "existing" in names
    assert "new" in names


def test_save_all_supervisor_procs_attribute_error(tmp_path):
    """If supervisor.procs raises AttributeError, result is (0, 0) with empty file."""
    path = str(tmp_path / "all.yaml")

    class BrokenSupervisor:
        @property
        def procs(self):
            raise AttributeError("no procs")

    written, skipped = save_all_process_specs(path, BrokenSupervisor())
    assert written == 0
    assert skipped == 0


def test_load_and_create_forwards_new_fields(tmp_path):
    """load_and_create passes work_dir, cpuset, cpu_limit, mem_limit to supervisor."""
    spec_file = tmp_path / "procs.yaml"
    spec_file.write_text(
        "name: foo\n"
        "host: h1\n"
        "exec_command: echo\n"
        "work_dir: /opt/robot\n"
        "cpuset: '0,1'\n"
        "cpu_limit: 1.5\n"
        "mem_limit: 1073741824\n"
    )

    mock_sup = MagicMock()
    from dpm.spec_io import load_and_create
    created, errors = load_and_create(str(spec_file), mock_sup)

    assert len(created) == 1
    assert len(errors) == 0
    mock_sup.create_proc.assert_called_once_with(
        "foo", "echo", "", "h1", False, False,
        work_dir="/opt/robot", cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824,
    )


def test_save_all_includes_new_fields():
    """save_all_process_specs includes work_dir, cpuset, cpu_limit, mem_limit."""
    import yaml
    import tempfile, os

    mock_proc = MagicMock()
    mock_proc.name = "foo"
    mock_proc.hostname = "h1"
    mock_proc.exec_command = "echo"
    mock_proc.group = "grp"
    mock_proc.auto_restart = False
    mock_proc.realtime = False
    mock_proc.work_dir = "/opt/robot"
    mock_proc.cpuset = "0,1"
    mock_proc.cpu_limit = 1.5
    mock_proc.mem_limit = 1073741824

    mock_sup = MagicMock()
    type(mock_sup).procs = PropertyMock(return_value={("h1", "foo"): mock_proc})

    from dpm.spec_io import save_all_process_specs
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        path = f.name

    try:
        written, skipped = save_all_process_specs(path, mock_sup)
        assert written == 1
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["work_dir"] == "/opt/robot"
        assert data["cpuset"] == "0,1"
        assert data["cpu_limit"] == 1.5
        assert data["mem_limit"] == 1073741824
    finally:
        os.unlink(path)
