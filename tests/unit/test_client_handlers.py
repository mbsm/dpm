"""Tests for Client LCM message handlers and thread-safe state updates."""

import time
import threading

import pytest

from dpm_msgs import host_info_t, host_procs_t, proc_info_t, proc_output_t


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _host_info(hostname="host1", cpu=0.5):
    msg = host_info_t()
    msg.timestamp = int(time.time() * 1e6)
    msg.hostname = hostname
    msg.ip = "127.0.0.1"
    msg.cpus = 4
    msg.cpu_usage = cpu
    msg.mem_total = 8000
    msg.mem_used = 4000
    msg.mem_free = 4000
    msg.mem_usage = 0.5
    msg.network_sent = 0.0
    msg.network_recv = 0.0
    msg.uptime = 1000
    return msg


def _proc_info(name, hostname="host1", state="R"):
    p = proc_info_t()
    p.name = name
    p.hostname = hostname
    p.group = ""
    p.state = state
    p.status = "running"
    p.errors = ""
    p.exec_command = "sleep 100"
    p.cpu = 0.0
    p.mem_rss = 0
    p.mem_vms = 0
    p.priority = 0
    p.pid = 1234
    p.ppid = 1
    p.auto_restart = False
    p.realtime = False
    p.exit_code = -1
    p.runtime = 0
    return p


def _host_procs(hostname, procs):
    msg = host_procs_t()
    msg.timestamp = int(time.time() * 1e6)
    msg.hostname = hostname
    msg.num_procs = len(procs)
    msg.procs = procs
    return msg


def _proc_output(name, stdout="", stderr="", hostname="host1"):
    msg = proc_output_t()
    msg.timestamp = int(time.time() * 1e6)
    msg.name = name
    msg.hostname = hostname
    msg.group = ""
    msg.stdout = stdout
    msg.stderr = stderr
    return msg


# ---------------------------------------------------------------------------
# host_info_handler
# ---------------------------------------------------------------------------

def test_host_info_stored_by_hostname(client):
    client.host_info_handler(None, _host_info("h1", cpu=0.3).encode())
    assert "h1" in client.hosts
    assert client.hosts["h1"].cpu_usage == pytest.approx(0.3)


def test_host_info_latest_message_wins(client):
    client.host_info_handler(None, _host_info("h1", cpu=0.3).encode())
    client.host_info_handler(None, _host_info("h1", cpu=0.9).encode())
    assert client.hosts["h1"].cpu_usage == pytest.approx(0.9)


def test_host_info_multiple_hosts_independent(client):
    client.host_info_handler(None, _host_info("h1", cpu=0.1).encode())
    client.host_info_handler(None, _host_info("h2", cpu=0.8).encode())
    assert "h1" in client.hosts
    assert "h2" in client.hosts
    assert client.hosts["h1"].cpu_usage != client.hosts["h2"].cpu_usage


def test_host_info_handler_thread_safe(client):
    """Many concurrent updates to the same host must not corrupt the dict."""
    errors = []

    def send(cpu):
        try:
            client.host_info_handler(None, _host_info("h1", cpu=cpu).encode())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=send, args=(i / 100,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert "h1" in client.hosts


# ---------------------------------------------------------------------------
# host_procs_handler
# ---------------------------------------------------------------------------

def test_host_procs_upserts_new_procs(client):
    msg = _host_procs("h1", [_proc_info("p1"), _proc_info("p2")])
    client.host_procs_handler(None, msg.encode())
    assert ("h1", "p1") in client.procs
    assert ("h1", "p2") in client.procs


def test_host_procs_removes_stale_procs_for_same_host(client):
    client.host_procs_handler(None, _host_procs("h1", [_proc_info("p1", "h1"), _proc_info("p2", "h1")]).encode())
    client.host_procs_handler(None, _host_procs("h1", [_proc_info("p1", "h1")]).encode())
    assert ("h1", "p1") in client.procs
    assert ("h1", "p2") not in client.procs


def test_host_procs_different_hosts_independent(client):
    client.host_procs_handler(None, _host_procs("h1", [_proc_info("p_h1", "h1")]).encode())
    client.host_procs_handler(None, _host_procs("h2", [_proc_info("p_h2", "h2")]).encode())
    # Empty update for h1 should not remove h2's procs
    client.host_procs_handler(None, _host_procs("h1", []).encode())
    assert ("h1", "p_h1") not in client.procs
    assert ("h2", "p_h2") in client.procs


def test_host_procs_empty_message_clears_host(client):
    client.host_procs_handler(None, _host_procs("h1", [_proc_info("p1", "h1")]).encode())
    client.host_procs_handler(None, _host_procs("h1", []).encode())
    assert ("h1", "p1") not in client.procs


def test_host_procs_preserves_proc_fields(client):
    p = _proc_info("p1", hostname="h1", state="F")
    client.host_procs_handler(None, _host_procs("h1", [p]).encode())
    stored = client.procs[("h1", "p1")]
    assert stored.hostname == "h1"
    assert stored.state == "F"


# ---------------------------------------------------------------------------
# proc_outputs_handler
# ---------------------------------------------------------------------------

def test_proc_output_stored_in_last_message(client):
    msg = _proc_output("p1", stdout="hello")
    client.proc_outputs_handler(None, msg.encode())
    last = client.get_proc_output_last("p1")
    assert last is not None
    assert last.stdout == "hello"


def test_proc_output_appended_to_buffer(client):
    client.proc_outputs_handler(None, _proc_output("p1", stdout="first").encode())
    client.proc_outputs_handler(None, _proc_output("p1", stdout="second").encode())
    buffers = client.proc_output_buffers
    assert "first" in buffers["p1"]
    assert "second" in buffers["p1"]


def test_proc_output_stderr_prefixed(client):
    client.proc_outputs_handler(None, _proc_output("p1", stderr="an error").encode())
    buffers = client.proc_output_buffers
    assert "[stderr]" in buffers["p1"]
    assert "an error" in buffers["p1"]


def test_proc_output_empty_message_ignored(client):
    client.proc_outputs_handler(None, _proc_output("p1", stdout="", stderr="").encode())
    assert "p1" not in client.proc_output_buffers


def test_proc_output_buffer_trimmed_to_2mb(client):
    MAX_BYTES = 2 * 1024 * 1024
    big = "x" * (MAX_BYTES + 5000)
    client.proc_outputs_handler(None, _proc_output("p1", stdout=big).encode())
    buffers = client.proc_output_buffers
    assert len(buffers["p1"]) <= MAX_BYTES


def test_proc_output_trim_increments_generation(client):
    MAX_BYTES = 2 * 1024 * 1024
    big = "x" * (MAX_BYTES + 5000)
    client.proc_outputs_handler(None, _proc_output("p1", stdout=big).encode())
    state = client._proc_output_states.get("p1")
    assert state is not None
    assert state.gen >= 1
