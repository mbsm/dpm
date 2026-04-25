"""Tests for Daemon output buffering, chunking, and thread-safe stream reading."""

import io
import threading

import pytest

from dpmd.daemon import MAX_OUTPUT_BUFFER, MAX_OUTPUT_CHUNK
from dpmd.processes import _OutBuf, create_process, monitor_process, stream_reader
from dpmd.telemetry import publish_procs_outputs
from dpm.constants import STATE_RUNNING
from dpm_msgs import log_chunk_t


def _setup_proc(agent, name="p1", stdout="", stderr="", subscribe=True):
    create_process(agent, name, "cmd", False, False, "")
    agent.processes[name].stdout = stdout
    agent.processes[name].stderr = stderr
    if subscribe:
        # Long-lived subscription so the publish path is not gated out.
        agent.output_subscriptions[name] = float("inf")
    agent.lc.publish.reset_mock()


# ---------------------------------------------------------------------------
# publish_procs_outputs — gating by subscription + chunking
# ---------------------------------------------------------------------------

def test_publishes_nothing_for_empty_buffers(agent):
    _setup_proc(agent)
    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()


def test_publishes_nothing_without_subscription(agent):
    """Silent-by-default: no subscription -> no wire publish even with content."""
    _setup_proc(agent, stdout="hello", subscribe=False)
    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()


def test_drains_buffers_when_no_subscribers(agent):
    """Without a subscriber the daemon still drains rings so they don't grow."""
    _setup_proc(agent, stdout="x" * 100, stderr="y" * 100, subscribe=False)
    publish_procs_outputs(agent)
    # Buffers were drained even though nothing was published.
    assert len(agent.processes["p1"].stdout) == 0
    assert len(agent.processes["p1"].stderr) == 0


def test_publishes_small_stdout_and_clears_buffer(agent):
    _setup_proc(agent, stdout="hello", stderr="err")
    publish_procs_outputs(agent)
    agent.lc.publish.assert_called_once()
    assert agent.processes["p1"].stdout == ""
    assert agent.processes["p1"].stderr == ""


def test_chunks_total_to_max_output_chunk(agent):
    """stdout + stderr together cap at MAX_OUTPUT_CHUNK per publish cycle."""
    big = "x" * (MAX_OUTPUT_CHUNK * 2)
    _setup_proc(agent, stdout=big)
    publish_procs_outputs(agent)

    _, encoded = agent.lc.publish.call_args[0]
    msg = log_chunk_t.decode(encoded)
    assert len(msg.content) == MAX_OUTPUT_CHUNK


def test_remainder_stays_in_buffer_after_chunk(agent):
    big = "x" * (MAX_OUTPUT_CHUNK + 100)
    _setup_proc(agent, stdout=big)
    publish_procs_outputs(agent)
    assert len(agent.processes["p1"].stdout) == 100


def test_second_publish_drains_remainder(agent):
    big = "x" * (MAX_OUTPUT_CHUNK + 50)
    _setup_proc(agent, stdout=big)
    publish_procs_outputs(agent)
    publish_procs_outputs(agent)
    assert agent.processes["p1"].stdout == ""


def test_published_message_carries_correct_metadata(agent):
    create_process(agent, "myproc", "cmd", False, False, "mygrp")
    agent.processes["myproc"].stdout = "data"
    agent.output_subscriptions["myproc"] = float("inf")
    agent.lc.publish.reset_mock()
    publish_procs_outputs(agent)

    _channel, encoded = agent.lc.publish.call_args[0]
    msg = log_chunk_t.decode(encoded)
    assert msg.name == "myproc"
    assert msg.hostname == agent.hostname
    assert msg.content == "data"
    assert msg.request_seq == 0  # unsolicited live publish
    assert msg.last is False     # live publishes are never "last"


def test_expired_subscription_blocks_publish(agent):
    """A subscription whose deadline has passed must not trigger publishes."""
    import time
    _setup_proc(agent, stdout="hello", subscribe=False)
    agent.output_subscriptions["p1"] = time.monotonic() - 1.0
    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()
    # And the expired entry was reaped on the way through.
    assert "p1" not in agent.output_subscriptions


# ---------------------------------------------------------------------------
# stream_reader — thread safety
# ---------------------------------------------------------------------------

def test_stream_reader_appends_all_lines():
    lock = threading.Lock()
    output_list = []
    content = "\n".join(f"line{i}" for i in range(500)) + "\n"
    stream = io.StringIO(content)

    stream_reader(stream, output_list, lock)

    assert len(output_list) == 500
    assert output_list[0] == "line0\n"
    assert output_list[-1] == "line499\n"


def test_stream_reader_concurrent_threads_no_lost_lines():
    """Two threads writing to the same list via the lock must not lose lines."""
    lock = threading.Lock()
    output_list = []
    n = 300

    content_a = "\n".join(f"a{i}" for i in range(n)) + "\n"
    content_b = "\n".join(f"b{i}" for i in range(n)) + "\n"

    t_a = threading.Thread(target=stream_reader, args=(io.StringIO(content_a), output_list, lock))
    t_b = threading.Thread(target=stream_reader, args=(io.StringIO(content_b), output_list, lock))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert len(output_list) == n * 2


def test_stream_reader_handles_empty_stream():
    lock = threading.Lock()
    output_list = []
    stream_reader(io.StringIO(""), output_list, lock)
    assert output_list == []


def test_stream_reader_skips_blank_lines():
    lock = threading.Lock()
    output_list = []
    stream_reader(io.StringIO("line1\n\nline2\n"), output_list, lock)
    assert len(output_list) == 2


def test_stream_reader_writes_to_log_file(tmp_path):
    """When passed a log_file, every line is also written to disk."""
    from dpmd.proc_logs import ProcessLogFile

    lock = threading.Lock()
    output_list = []
    log_path = tmp_path / "p.log"
    log_file = ProcessLogFile(str(log_path), max_bytes=10_000, backups=1)

    stream_reader(io.StringIO("a\nb\n"), output_list, lock, log_file=log_file)
    log_file.close()

    assert log_path.read_text() == "a\nb\n"


# ---------------------------------------------------------------------------
# monitor_process — output drain
# ---------------------------------------------------------------------------

def test_monitor_process_drains_stdout_lines_to_buffer(agent):
    import threading
    from unittest.mock import MagicMock
    create_process(agent, "p1", "sleep 100", False, False, "")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    agent.processes["p1"].proc = fake_proc
    agent.processes["p1"].state = STATE_RUNNING

    # Simulate reader thread having accumulated lines (normally set by start_process)
    agent.processes["p1"].output_lock = threading.Lock()
    agent.processes["p1"].stdout_lines = ["line1\n", "line2\n"]
    agent.processes["p1"].stderr_lines = []

    monitor_process(agent, "p1")

    assert agent.processes["p1"].stdout == "line1\nline2\n"
    assert agent.processes["p1"].stdout_lines == []


def test_monitor_process_skips_if_not_running(agent):
    create_process(agent, "p1", "cmd", False, False, "")
    monitor_process(agent, "p1")


def test_monitor_process_skips_unknown_name(agent):
    monitor_process(agent, "no_such_proc")


# ---------------------------------------------------------------------------
# _OutBuf — chunked FIFO semantics
# ---------------------------------------------------------------------------

def test_outbuf_append_and_take_roundtrips():
    b = _OutBuf()
    b.append("hello ", max_size=1024)
    b.append("world", max_size=1024)
    assert len(b) == 11
    assert bool(b) is True
    assert b.take(1024) == "hello world"
    assert len(b) == 0
    assert bool(b) is False


def test_outbuf_take_splits_chunks():
    b = _OutBuf()
    b.append("abc", max_size=1024)
    b.append("defgh", max_size=1024)
    assert b.take(4) == "abcd"
    assert len(b) == 4
    assert b.take(10) == "efgh"
    assert len(b) == 0


def test_outbuf_append_caps_at_max_size():
    b = _OutBuf()
    b.append("x" * 100, max_size=80)
    assert len(b) == 80
    # only the trailing 80 bytes survive
    assert b.take(80) == "x" * 80


def test_outbuf_append_trims_old_front_chunks():
    b = _OutBuf()
    b.append("a" * 60, max_size=100)
    b.append("b" * 60, max_size=100)
    # 60 + 60 = 120; must trim to 100 from the front
    assert len(b) == 100
    result = b.take(100)
    assert result.endswith("b" * 60)
    assert "a" in result  # some of the older chunk remains
    assert len(result) == 100


def test_outbuf_str_assignment_preserves_buffer_identity():
    """Proc.__setattr__ must replace contents in place, not swap the object."""
    from dpmd.processes import Proc

    p = Proc()
    original_buf = p.stdout
    p.stdout = "new content"
    # Same _OutBuf instance, updated contents
    assert p.stdout is original_buf
    assert p.stdout == "new content"
    p.stdout = ""
    assert p.stdout == ""
    assert len(p.stdout) == 0


def test_publish_drains_large_buffer_without_rebuild(agent):
    """Regression: large buffer draining should not rebuild the tail each cycle."""
    big = "x" * (MAX_OUTPUT_CHUNK * 3 + 17)
    _setup_proc(agent, stdout=big)
    # Drain in three full chunks + a tail
    publish_procs_outputs(agent)
    publish_procs_outputs(agent)
    publish_procs_outputs(agent)
    assert len(agent.processes["p1"].stdout) == 17
    publish_procs_outputs(agent)
    assert agent.processes["p1"].stdout == ""


# ---------------------------------------------------------------------------
# Reader thread join order on restart
# ---------------------------------------------------------------------------

def test_start_process_joins_stale_reader_threads(agent, monkeypatch):
    """A restart with leftover reader threads must join them before spawning new ones."""
    from unittest.mock import MagicMock
    from dpmd.processes import start_process

    create_process(agent, "p1", "cmd", False, False, "")

    # Simulate leftover reader threads from a prior run
    joined = {"stdout": False, "stderr": False}

    class FakeThread:
        def __init__(self, which):
            self.which = which
        def join(self, timeout=None):
            joined[self.which] = True

    agent.processes["p1"].stdout_thread = FakeThread("stdout")
    agent.processes["p1"].stderr_thread = FakeThread("stderr")

    # Prevent Popen from actually running a process
    def fake_popen(*args, **kwargs):
        m = MagicMock()
        m.pid = 12345
        m.stdout = io.StringIO("")
        m.stderr = io.StringIO("")
        return m
    monkeypatch.setattr("dpmd.processes.psutil.Popen", fake_popen)
    monkeypatch.setattr("dpmd.processes.psutil.Process", lambda pid: MagicMock())

    start_process(agent, "p1")

    assert joined["stdout"] is True
    assert joined["stderr"] is True
    # And the thread slots were cleared before the new threads were assigned
    # (new threads are real threading.Thread instances from start_process).
    new_t = agent.processes["p1"].stdout_thread
    assert new_t is None or not isinstance(new_t, FakeThread)
