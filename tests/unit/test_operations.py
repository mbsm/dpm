"""Tests for the shared domain operations (move, launch, create_from_spec)."""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from dpm import operations


def _make_proc(name, host, state="T", group=""):
    p = MagicMock()
    p.name = name
    p.hostname = host
    p.state = state
    p.group = group
    p.exec_command = "cmd"
    p.auto_restart = False
    p.realtime = False
    p.isolated = False
    p.work_dir = ""
    p.cpuset = ""
    p.cpu_limit = 0.0
    p.mem_limit = 0
    return p


def _make_host(name):
    h = MagicMock()
    h.hostname = name
    return h


def _client_with(hosts=None, procs=None):
    c = MagicMock()
    c._procs = {}
    c._hosts = {}
    for p in procs or []:
        c._procs[(p.hostname, p.name)] = p
    for h in hosts or []:
        c._hosts[h.hostname] = h
    type(c).procs = PropertyMock(return_value=c._procs)
    type(c).hosts = PropertyMock(return_value=c._hosts)
    return c


# ---------------------------------------------------------------------------
# Progress sinks
# ---------------------------------------------------------------------------

def test_null_progress_is_silent():
    p = operations.Progress()
    p.info("nothing")  # no raise
    p.warn("nothing")


def test_callback_progress_routes_level_and_msg():
    calls = []
    p = operations.CallbackProgress(lambda level, msg: calls.append((level, msg)))
    p.info("hi")
    p.warn("uh oh")
    assert calls == [("info", "hi"), ("warn", "uh oh")]


# ---------------------------------------------------------------------------
# move_process
# ---------------------------------------------------------------------------

def test_move_source_not_found():
    c = _client_with(hosts=[_make_host("h1"), _make_host("h2")])
    ok, msg = operations.move_process(c, "missing", "h1", "missing", "h2")
    assert ok is False
    assert "not found" in msg


def test_move_dest_host_not_responding():
    c = _client_with(
        hosts=[_make_host("h1")],
        procs=[_make_proc("p", "h1")],
    )
    ok, msg = operations.move_process(c, "p", "h1", "p", "h2")
    assert ok is False
    assert "not responding" in msg


def test_move_dest_already_has_proc():
    c = _client_with(
        hosts=[_make_host("h1"), _make_host("h2")],
        procs=[_make_proc("p", "h1"), _make_proc("p", "h2")],
    )
    ok, msg = operations.move_process(c, "p", "h1", "p", "h2")
    assert ok is False
    assert "already exists" in msg


@patch("dpm.operations.wait_for_state", return_value=True)
def test_move_ready_process_creates_and_deletes(mock_wait):
    c = _client_with(
        hosts=[_make_host("h1"), _make_host("h2")],
        procs=[_make_proc("p", "h1", state="T")],
    )
    c.create_proc = MagicMock(
        side_effect=lambda *a, **k: c._procs.update(
            {("h2", "p"): _make_proc("p", "h2", state="T")}
        )
    )
    c.del_proc = MagicMock()
    ok, msg = operations.move_process(c, "p", "h1", "p", "h2")
    assert ok is True
    assert "Moved" in msg
    c.create_proc.assert_called_once()
    c.del_proc.assert_called_once_with("p", "h1")


@patch("dpm.operations.wait_for_state", return_value=True)
def test_move_rolls_back_source_when_dest_creation_fails(mock_wait):
    c = _client_with(
        hosts=[_make_host("h1"), _make_host("h2")],
        procs=[_make_proc("p", "h1", state="R")],
    )
    c.create_proc = MagicMock()  # doesn't update _procs → dst never appears
    c.start_proc = MagicMock()
    c.stop_proc = MagicMock()
    c.del_proc = MagicMock()

    ok, msg = operations.move_process(c, "p", "h1", "p", "h2")

    assert ok is False
    assert "Failed to create" in msg
    # Rollback: source was running, so it must be restarted
    c.start_proc.assert_called_once_with("p", "h1")
    # And source must NOT have been deleted
    c.del_proc.assert_not_called()


# ---------------------------------------------------------------------------
# run_launch
# ---------------------------------------------------------------------------

def test_run_launch_empty_groups():
    ok, msg = operations.run_launch(MagicMock(), {"name": "t", "timeout": 5, "groups": {}})
    assert ok is False
    assert "No groups" in msg


def test_run_launch_reports_cycle():
    script = {
        "name": "t",
        "timeout": 5,
        "groups": {
            "a": {"requires": ["b"], "after": []},
            "b": {"requires": ["a"], "after": []},
        },
    }
    ok, msg = operations.run_launch(MagicMock(), script)
    assert ok is False
    assert "cycle" in msg.lower() or "Invalid" in msg


@patch("dpm.operations.wait_for_state", return_value=True)
def test_run_launch_starts_each_wave_in_order(mock_wait):
    proc_a = _make_proc("svc_a", "h1", state="R", group="core")
    proc_b = _make_proc("svc_b", "h1", state="R", group="ui")
    c = _client_with(
        hosts=[_make_host("h1")],
        procs=[proc_a, proc_b],
    )
    c.start_group = MagicMock()

    script = {
        "name": "demo",
        "timeout": 5,
        "processes": [],
        "groups": {
            "core": {"requires": [], "after": []},
            "ui": {"requires": ["core"], "after": []},
        },
    }
    ok, _msg = operations.run_launch(c, script)
    assert ok is True
    # Two waves → two start_group fan-outs
    calls = [call.args[0] for call in c.start_group.call_args_list]
    assert calls == ["core", "ui"]


# ---------------------------------------------------------------------------
# create_from_spec
# ---------------------------------------------------------------------------

def test_create_from_spec_happy_path():
    c = MagicMock()
    spec = {"name": "p1", "host": "h1", "exec_command": "sleep 1"}
    label = operations.create_from_spec(c, spec)
    assert label == "p1@h1"
    c.create_proc.assert_called_once()


def test_create_from_spec_rejects_missing_name():
    c = MagicMock()
    with pytest.raises(ValueError, match="name"):
        operations.create_from_spec(c, {"host": "h1", "exec_command": "cmd"})
