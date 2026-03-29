"""Tests for NodeAgent output buffering, chunking, and thread-safe stream reading."""

import io
import threading

import pytest

from dpm.node.node import MAX_OUTPUT_CHUNK, STATE_RUNNING, stream_reader
from dpm_msgs import proc_output_t


def _setup_proc(node_agent, name="p1", stdout="", stderr=""):
    node_agent.create_process(name, "cmd", False, False, "")
    node_agent.processes[name]["stdout"] = stdout
    node_agent.processes[name]["stderr"] = stderr
    node_agent.lc.publish.reset_mock()


# ---------------------------------------------------------------------------
# publish_procs_outputs — chunking (fix #3)
# ---------------------------------------------------------------------------

def test_publishes_nothing_for_empty_buffers(node_agent):
    _setup_proc(node_agent)
    node_agent.publish_procs_outputs()
    node_agent.lc.publish.assert_not_called()


def test_publishes_small_stdout_and_clears_buffer(node_agent):
    _setup_proc(node_agent, stdout="hello", stderr="err")
    node_agent.publish_procs_outputs()
    node_agent.lc.publish.assert_called_once()
    assert node_agent.processes["p1"]["stdout"] == ""
    assert node_agent.processes["p1"]["stderr"] == ""


def test_chunks_stdout_to_max_output_chunk(node_agent):
    big = "x" * (MAX_OUTPUT_CHUNK * 2)
    _setup_proc(node_agent, stdout=big)
    node_agent.publish_procs_outputs()

    _, encoded = node_agent.lc.publish.call_args[0]
    msg = proc_output_t.decode(encoded)
    assert len(msg.stdout) == MAX_OUTPUT_CHUNK


def test_remainder_stays_in_buffer_after_chunk(node_agent):
    big = "x" * (MAX_OUTPUT_CHUNK + 100)
    _setup_proc(node_agent, stdout=big)
    node_agent.publish_procs_outputs()
    assert len(node_agent.processes["p1"]["stdout"]) == 100


def test_second_publish_drains_remainder(node_agent):
    big = "x" * (MAX_OUTPUT_CHUNK + 50)
    _setup_proc(node_agent, stdout=big)
    node_agent.publish_procs_outputs()
    node_agent.publish_procs_outputs()
    assert node_agent.processes["p1"]["stdout"] == ""


def test_chunks_stderr_independently(node_agent):
    big_err = "e" * (MAX_OUTPUT_CHUNK + 200)
    _setup_proc(node_agent, stderr=big_err)
    node_agent.publish_procs_outputs()

    _, encoded = node_agent.lc.publish.call_args[0]
    msg = proc_output_t.decode(encoded)
    assert len(msg.stderr) == MAX_OUTPUT_CHUNK
    assert len(node_agent.processes["p1"]["stderr"]) == 200


def test_published_message_carries_correct_metadata(node_agent):
    node_agent.create_process("myproc", "cmd", False, False, "mygrp")
    node_agent.processes["myproc"]["stdout"] = "data"
    node_agent.lc.publish.reset_mock()
    node_agent.publish_procs_outputs()

    channel, encoded = node_agent.lc.publish.call_args[0]
    msg = proc_output_t.decode(encoded)
    assert msg.name == "myproc"
    assert msg.hostname == node_agent.hostname
    assert msg.group == "mygrp"
    assert msg.stdout == "data"


# ---------------------------------------------------------------------------
# stream_reader — thread safety (fix #4)
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
    # blank lines are skipped (only non-empty lines after rstrip are appended)
    assert len(output_list) == 2


# ---------------------------------------------------------------------------
# monitor_process — output drain
# ---------------------------------------------------------------------------

def test_monitor_process_drains_stdout_lines_to_buffer(node_agent):
    from unittest.mock import MagicMock
    node_agent.create_process("p1", "sleep 100", False, False, "")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    node_agent.processes["p1"]["proc"] = fake_proc
    node_agent.processes["p1"]["state"] = STATE_RUNNING

    # Simulate reader thread having accumulated lines
    node_agent.processes["p1"]["stdout_lines"] = ["line1\n", "line2\n"]
    node_agent.processes["p1"]["stderr_lines"] = []

    node_agent.monitor_process("p1")

    assert node_agent.processes["p1"]["stdout"] == "line1\nline2\n"
    assert node_agent.processes["p1"]["stdout_lines"] == []


def test_monitor_process_skips_if_not_running(node_agent):
    node_agent.create_process("p1", "cmd", False, False, "")
    # proc is None → monitor should return immediately without error
    node_agent.monitor_process("p1")


def test_monitor_process_skips_unknown_name(node_agent):
    node_agent.monitor_process("no_such_proc")
