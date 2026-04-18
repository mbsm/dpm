"""Tests for DPM CLI commands with mocked Client."""

import argparse
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from dpm.cli.cli import build_parser, parse_name_at_host, parse_at_host, parse_name_optional_host
from dpm.cli import commands


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def test_parse_name_at_host():
    assert parse_name_at_host("cam@jet1") == ("cam", "jet1")


def test_parse_name_at_host_missing_host():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_name_at_host("cam@")


def test_parse_name_at_host_missing_name():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_name_at_host("@jet1")


def test_parse_name_at_host_no_at():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_name_at_host("cam")


def test_parse_at_host():
    assert parse_at_host("@jet1") == "jet1"


def test_parse_at_host_no_prefix():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_at_host("jet1")


def test_parse_name_optional_host_with_host():
    assert parse_name_optional_host("cam@jet1") == ("cam", "jet1")


def test_parse_name_optional_host_without_host():
    assert parse_name_optional_host("cam") == ("cam", None)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_host(hostname="jet1"):
    return types.SimpleNamespace(
        hostname=hostname,
        ip="10.0.0.1",
        cpus=8,
        cpu_usage=0.12,
        mem_total=16_000_000,
        mem_used=8_000_000,
        mem_free=8_000_000,
        mem_usage=0.5,
        network_sent=100.0,
        network_recv=50.0,
        uptime=100_000,
        timestamp=int(time.time() * 1e6),
    )


def _make_proc(name="svc", hostname="jet1", state="R", pid=1234):
    return types.SimpleNamespace(
        name=name,
        hostname=hostname,
        group="core",
        state=state,
        pid=pid,
        cpu=0.05,
        mem_rss=131072,
        exec_command="sleep 100",
        auto_restart=False,
        realtime=False,
        runtime=3600,
        exit_code=0,
    )


def _inject(client, hosts=None, procs=None):
    """Populate client internals to simulate telemetry."""
    if hosts:
        for h in hosts:
            client._hosts[h.hostname] = h
    if procs:
        for p in procs:
            client._procs[(p.hostname, p.name)] = p


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@patch("dpm.cli.commands.wait_for_telemetry", return_value=False)
def test_status_no_agents(mock_wait, client, capsys):
    args = argparse.Namespace(command="status", host=None)
    rc = commands.cmd_status(client, args)
    assert rc == 2
    assert "No agents" in capsys.readouterr().err


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_status_shows_hosts_and_procs(mock_wait, client, capsys):
    _inject(client,
            hosts=[_make_host("jet1")],
            procs=[_make_proc("cam", "jet1")])
    args = argparse.Namespace(command="status", host=None)
    rc = commands.cmd_status(client, args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "jet1" in out
    assert "cam@jet1" in out


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_status_host_filter(mock_wait, client, capsys):
    _inject(client,
            hosts=[_make_host("jet1"), _make_host("jet2")],
            procs=[_make_proc("a", "jet1"), _make_proc("b", "jet2")])
    args = argparse.Namespace(command="status", host="jet1")
    rc = commands.cmd_status(client, args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "a@jet1" in out
    assert "b@jet2" not in out


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_status_host_not_found(mock_wait, client, capsys):
    _inject(client, hosts=[_make_host("jet1")])
    args = argparse.Namespace(command="status", host="nonexistent")
    rc = commands.cmd_status(client, args)
    assert rc == 1


# ---------------------------------------------------------------------------
# start / stop / restart
# ---------------------------------------------------------------------------

@patch("dpm.cli.commands.wait_for_state", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_start_calls_client(mock_wait, mock_state, client, capsys):
    _inject(client, procs=[_make_proc("cam", "jet1", state="T")])
    client.start_proc = MagicMock()
    args = argparse.Namespace(command="start", name="cam", host="jet1")
    rc = commands.cmd_start(client, args)
    assert rc == 0
    client.start_proc.assert_called_once_with("cam", "jet1")
    assert "Started" in capsys.readouterr().out


@patch("dpm.cli.commands.wait_for_state", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_stop_calls_client(mock_wait, mock_state, client, capsys):
    _inject(client, procs=[_make_proc("cam", "jet1")])
    client.stop_proc = MagicMock()
    args = argparse.Namespace(command="stop", name="cam", host="jet1")
    rc = commands.cmd_stop(client, args)
    assert rc == 0
    client.stop_proc.assert_called_once_with("cam", "jet1")


@patch("dpm.cli.commands.wait_for_state", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_restart_calls_stop_then_start(mock_wait, mock_state, client):
    _inject(client, procs=[_make_proc("cam", "jet1")])
    client.stop_proc = MagicMock()
    client.start_proc = MagicMock()
    args = argparse.Namespace(command="restart", name="cam", host="jet1")
    rc = commands.cmd_restart(client, args)
    assert rc == 0
    client.stop_proc.assert_called_once_with("cam", "jet1")
    client.start_proc.assert_called_once_with("cam", "jet1")


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_start_proc_not_found(mock_wait, client, capsys):
    args = argparse.Namespace(command="start", name="nope", host="jet1")
    rc = commands.cmd_start(client, args)
    assert rc == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# create / delete
# ---------------------------------------------------------------------------

@patch("dpm.cli.commands.wait_for_state", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_create_calls_client(mock_wait, mock_state, client, capsys):
    client.create_proc = MagicMock()
    args = argparse.Namespace(
        command="create", name="svc", host="jet1",
        cmd="echo hi", group="core", auto_restart=True, realtime=False,
        isolated=False, work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
    )
    rc = commands.cmd_create(client, args)
    assert rc == 0
    client.create_proc.assert_called_once_with(
        "svc", "echo hi", "core", "jet1", True, False,
        work_dir="", cpuset="", cpu_limit=0.0, mem_limit=0,
        isolated=False,
    )


@patch("dpm.cli.commands.wait_for_proc_gone", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_delete_calls_stop_and_del(mock_wait, mock_gone, client, capsys):
    _inject(client, procs=[_make_proc("cam", "jet1")])
    client.stop_proc = MagicMock()
    client.del_proc = MagicMock()
    args = argparse.Namespace(command="delete", name="cam", host="jet1")
    rc = commands.cmd_delete(client, args)
    assert rc == 0
    client.stop_proc.assert_called_once()
    client.del_proc.assert_called_once()
    assert "Deleted" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------

@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_load_delegates_to_spec_io(mock_wait, client, capsys):
    with patch("dpm.spec_io.load_and_create", return_value=(["a@h1"], [])) as mock_lc:
        args = argparse.Namespace(command="load", path="specs.yaml")
        rc = commands.cmd_load(client, args)
    assert rc == 0
    mock_lc.assert_called_once()
    assert "1/1" in capsys.readouterr().out


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_save_delegates_to_spec_io(mock_wait, client, capsys):
    _inject(client, hosts=[_make_host()])
    with patch("dpm.spec_io.save_all_process_specs", return_value=(3, 0)) as mock_save:
        args = argparse.Namespace(command="save", path="out.yaml", append=False)
        rc = commands.cmd_save(client, args)
    assert rc == 0
    mock_save.assert_called_once()
    assert "3" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# start-all / stop-all
# ---------------------------------------------------------------------------

@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_start_all_sends_to_all_procs(mock_wait, client, capsys):
    _inject(client, hosts=[_make_host()],
            procs=[_make_proc("a", "jet1", "T"), _make_proc("b", "jet1", "T")])
    client.start_proc = MagicMock()
    args = argparse.Namespace(command="start-all")
    rc = commands.cmd_start_all(client, args)
    assert rc == 0
    assert client.start_proc.call_count == 2
    assert "2" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

@patch("dpm.cli.commands.wait_for_state", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_move_ready_process(mock_wait, mock_state, client, capsys):
    """Move a READY process: create on dest, delete from source."""
    _inject(client,
            hosts=[_make_host("jet1"), _make_host("jet2")],
            procs=[_make_proc("cam", "jet1", state="T")])
    client.create_proc = MagicMock()
    client.del_proc = MagicMock()
    # After create, simulate proc appearing on jet2
    original_procs = client._procs.copy()
    def _side_effect(*a, **kw):
        client._procs[("jet2", "cam")] = _make_proc("cam", "jet2", state="T")
    client.create_proc.side_effect = _side_effect

    args = argparse.Namespace(command="move",
                              src_name="cam", src_host="jet1",
                              dst_name="cam", dst_host="jet2")
    rc = commands.cmd_move(client, args)
    assert rc == 0
    client.create_proc.assert_called_once()
    client.del_proc.assert_called_once_with("cam", "jet1")
    assert "Moved" in capsys.readouterr().out


@patch("dpm.cli.commands.wait_for_state", return_value=True)
@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_move_running_process_stops_first(mock_wait, mock_state, client, capsys):
    """Move a RUNNING process: stop on source, create+start on dest, delete source."""
    _inject(client,
            hosts=[_make_host("jet1"), _make_host("jet2")],
            procs=[_make_proc("cam", "jet1", state="R")])
    client.stop_proc = MagicMock()
    client.start_proc = MagicMock()
    client.del_proc = MagicMock()
    client.create_proc = MagicMock(
        side_effect=lambda *a, **kw: client._procs.update(
            {("jet2", "cam"): _make_proc("cam", "jet2", state="T")}))

    args = argparse.Namespace(command="move",
                              src_name="cam", src_host="jet1",
                              dst_name="cam", dst_host="jet2")
    rc = commands.cmd_move(client, args)
    assert rc == 0
    client.stop_proc.assert_called_once_with("cam", "jet1")
    client.start_proc.assert_called_once_with("cam", "jet2")
    client.del_proc.assert_called_once_with("cam", "jet1")


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_move_source_not_found(mock_wait, client, capsys):
    _inject(client, hosts=[_make_host("jet1"), _make_host("jet2")])
    args = argparse.Namespace(command="move",
                              src_name="nope", src_host="jet1",
                              dst_name="nope", dst_host="jet2")
    rc = commands.cmd_move(client, args)
    assert rc == 1
    assert "not found" in capsys.readouterr().err


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_move_dest_host_not_responding(mock_wait, client, capsys):
    _inject(client,
            hosts=[_make_host("jet1")],
            procs=[_make_proc("cam", "jet1")])
    args = argparse.Namespace(command="move",
                              src_name="cam", src_host="jet1",
                              dst_name="cam", dst_host="jet99")
    rc = commands.cmd_move(client, args)
    assert rc == 1
    assert "not responding" in capsys.readouterr().err


@patch("dpm.cli.commands.wait_for_telemetry", return_value=True)
def test_move_with_rename(mock_wait, client, capsys):
    """dpm move cam@jet1 cam2@jet2 — rename during move."""
    _inject(client,
            hosts=[_make_host("jet1"), _make_host("jet2")],
            procs=[_make_proc("cam", "jet1", state="T")])
    client.create_proc = MagicMock(
        side_effect=lambda *a, **kw: client._procs.update(
            {("jet2", "cam2"): _make_proc("cam2", "jet2", state="T")}))
    client.del_proc = MagicMock()

    args = argparse.Namespace(command="move",
                              src_name="cam", src_host="jet1",
                              dst_name="cam2", dst_host="jet2")
    rc = commands.cmd_move(client, args)
    assert rc == 0
    # Verify create used the new name
    call_args = client.create_proc.call_args
    assert call_args[0][0] == "cam2"  # new name
    assert "cam2@jet2" in capsys.readouterr().out


def test_argparse_move():
    parser = build_parser()
    args = parser.parse_args(["move", "cam@jet1", "@jet2"])
    assert args.command == "move"
    assert args.source == "cam@jet1"
    assert args.dest == "@jet2"


def test_argparse_move_with_rename():
    parser = build_parser()
    args = parser.parse_args(["move", "cam@jet1", "cam2@jet2"])
    assert args.source == "cam@jet1"
    assert args.dest == "cam2@jet2"


# ---------------------------------------------------------------------------
# argparse integration
# ---------------------------------------------------------------------------

def test_argparse_status():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_argparse_start():
    parser = build_parser()
    args = parser.parse_args(["start", "cam@jet1"])
    assert args.command == "start"
    assert args.target == "cam@jet1"


def test_argparse_create():
    parser = build_parser()
    args = parser.parse_args(["create", "svc@jet1", "--cmd", "echo hi", "-g", "core", "--auto-restart"])
    assert args.command == "create"
    assert args.cmd == "echo hi"
    assert args.group == "core"
    assert args.auto_restart is True


def test_argparse_logs():
    parser = build_parser()
    args = parser.parse_args(["logs", "cam@jet1"])
    assert args.target == "cam@jet1"


def test_argparse_no_command():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_argparse_launch():
    from dpm.cli.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["launch", "system.yaml"])
    assert args.command == "launch"
    assert args.path == "system.yaml"


def test_argparse_shutdown():
    from dpm.cli.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["shutdown", "system.yaml"])
    assert args.command == "shutdown"
    assert args.path == "system.yaml"


def test_argparse_create_with_new_fields():
    from dpm.cli.cli import build_parser, _resolve_args
    parser = build_parser()
    args = parser.parse_args([
        "create", "foo@host1", "--cmd", "echo hi",
        "--work-dir", "/opt/robot",
        "--cpuset", "0,1",
        "--cpu-limit", "1.5",
        "--mem-limit", "1073741824",
    ])
    args = _resolve_args(args)
    assert args.name == "foo"
    assert args.host == "host1"
    assert args.work_dir == "/opt/robot"
    assert args.cpuset == "0,1"
    assert args.cpu_limit == 1.5
    assert args.mem_limit == 1073741824


def test_create_forwards_new_fields_to_client():
    from unittest.mock import MagicMock, patch
    from dpm.cli.commands import cmd_create

    mock_sup = MagicMock()
    mock_sup.hosts = {"host1": MagicMock()}
    mock_sup.procs = {}

    args = MagicMock()
    args.command = "create"
    args.name = "foo"
    args.host = "host1"
    args.cmd = "echo hi"
    args.group = "grp"
    args.auto_restart = False
    args.realtime = False
    args.isolated = False
    args.work_dir = "/opt/robot"
    args.cpuset = "0,1"
    args.cpu_limit = 1.5
    args.mem_limit = 1073741824

    with patch("dpm.cli.commands.wait_for_telemetry", return_value=True), \
         patch("dpm.cli.commands.wait_for_state", return_value=True):
        cmd_create(mock_sup, args)

    mock_sup.create_proc.assert_called_once_with(
        "foo", "echo hi", "grp", "host1", False, False,
        work_dir="/opt/robot", cpuset="0,1", cpu_limit=1.5, mem_limit=1073741824,
        isolated=False,
    )
