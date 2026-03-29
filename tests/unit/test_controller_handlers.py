"""Tests for Controller LCM message handlers and thread-safe state updates."""

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

def test_host_info_stored_by_hostname(controller):
    controller.host_info_handler(None, _host_info("h1", cpu=0.3).encode())
    assert "h1" in controller.hosts
    assert controller.hosts["h1"].cpu_usage == pytest.approx(0.3)


def test_host_info_latest_message_wins(controller):
    controller.host_info_handler(None, _host_info("h1", cpu=0.3).encode())
    controller.host_info_handler(None, _host_info("h1", cpu=0.9).encode())
    assert controller.hosts["h1"].cpu_usage == pytest.approx(0.9)


def test_host_info_multiple_hosts_independent(controller):
    controller.host_info_handler(None, _host_info("h1", cpu=0.1).encode())
    controller.host_info_handler(None, _host_info("h2", cpu=0.8).encode())
    assert "h1" in controller.hosts
    assert "h2" in controller.hosts
    assert controller.hosts["h1"].cpu_usage != controller.hosts["h2"].cpu_usage


def test_host_info_handler_thread_safe(controller):
    """Many concurrent updates to the same host must not corrupt the dict."""
    errors = []

    def send(cpu):
        try:
            controller.host_info_handler(None, _host_info("h1", cpu=cpu).encode())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=send, args=(i / 100,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert "h1" in controller.hosts


# ---------------------------------------------------------------------------
# host_procs_handler
# ---------------------------------------------------------------------------

def test_host_procs_upserts_new_procs(controller):
    msg = _host_procs("h1", [_proc_info("p1"), _proc_info("p2")])
    controller.host_procs_handler(None, msg.encode())
    assert "p1" in controller.procs
    assert "p2" in controller.procs


def test_host_procs_removes_stale_procs_for_same_host(controller):
    controller.host_procs_handler(None, _host_procs("h1", [_proc_info("p1", "h1"), _proc_info("p2", "h1")]).encode())
    controller.host_procs_handler(None, _host_procs("h1", [_proc_info("p1", "h1")]).encode())
    assert "p1" in controller.procs
    assert "p2" not in controller.procs


def test_host_procs_different_hosts_independent(controller):
    controller.host_procs_handler(None, _host_procs("h1", [_proc_info("p_h1", "h1")]).encode())
    controller.host_procs_handler(None, _host_procs("h2", [_proc_info("p_h2", "h2")]).encode())
    # Empty update for h1 should not remove h2's procs
    controller.host_procs_handler(None, _host_procs("h1", []).encode())
    assert "p_h1" not in controller.procs
    assert "p_h2" in controller.procs


def test_host_procs_empty_message_clears_host(controller):
    controller.host_procs_handler(None, _host_procs("h1", [_proc_info("p1", "h1")]).encode())
    controller.host_procs_handler(None, _host_procs("h1", []).encode())
    assert "p1" not in controller.procs


def test_host_procs_preserves_proc_fields(controller):
    p = _proc_info("p1", hostname="h1", state="F")
    controller.host_procs_handler(None, _host_procs("h1", [p]).encode())
    stored = controller.procs["p1"]
    assert stored.hostname == "h1"
    assert stored.state == "F"


# ---------------------------------------------------------------------------
# proc_outputs_handler
# ---------------------------------------------------------------------------

def test_proc_output_stored_in_last_message(controller):
    msg = _proc_output("p1", stdout="hello")
    controller.proc_outputs_handler(None, msg.encode())
    last = controller.get_proc_output_last("p1")
    assert last is not None
    assert last.stdout == "hello"


def test_proc_output_appended_to_buffer(controller):
    controller.proc_outputs_handler(None, _proc_output("p1", stdout="first").encode())
    controller.proc_outputs_handler(None, _proc_output("p1", stdout="second").encode())
    buffers = controller.proc_output_buffers
    assert "first" in buffers["p1"]
    assert "second" in buffers["p1"]


def test_proc_output_stderr_prefixed(controller):
    controller.proc_outputs_handler(None, _proc_output("p1", stderr="an error").encode())
    buffers = controller.proc_output_buffers
    assert "[stderr]" in buffers["p1"]
    assert "an error" in buffers["p1"]


def test_proc_output_empty_message_ignored(controller):
    controller.proc_outputs_handler(None, _proc_output("p1", stdout="", stderr="").encode())
    assert "p1" not in controller.proc_output_buffers


def test_proc_output_buffer_trimmed_to_2mb(controller):
    MAX_BYTES = 2 * 1024 * 1024
    big = "x" * (MAX_BYTES + 5000)
    controller.proc_outputs_handler(None, _proc_output("p1", stdout=big).encode())
    buffers = controller.proc_output_buffers
    assert len(buffers["p1"]) <= MAX_BYTES


def test_proc_output_trim_increments_generation(controller):
    MAX_BYTES = 2 * 1024 * 1024
    big = "x" * (MAX_BYTES + 5000)
    controller.proc_outputs_handler(None, _proc_output("p1", stdout=big).encode())
    gen = controller._proc_output_buffer_gen.get("p1", 0)
    assert gen >= 1
