"""Tests for the disk-tail output publisher and stream reader."""

import io
import os

import pytest

from dpm.constants import STATE_RUNNING
from dpmd.limits import MAX_OUTPUT_CHUNK
from dpmd.processes import create_process, stream_reader
from dpmd.proc_logs import ProcessLogFile
from dpmd.telemetry import publish_procs_outputs
from dpm_msgs import log_chunk_t


def _make_proc(agent, name="p1", subscribe=True, log_path=None):
    """Register a process with a real on-disk log handle."""
    if log_path is None:
        log_path = os.path.join(agent.process_log_dir or "/tmp", f"{name}.log")
    create_process(agent, name, "cmd", False, False, "")
    log_file = ProcessLogFile(log_path, max_bytes=10_000_000, backups=2)
    agent.processes[name].log_file = log_file
    if subscribe:
        agent.output_subscriptions[name] = float("inf")
    agent.lc.publish.reset_mock()
    return log_file


def _published_chunks(agent):
    chunks = []
    for call in agent.lc.publish.call_args_list:
        _channel, encoded = call.args
        try:
            chunks.append(log_chunk_t.decode(encoded))
        except Exception:
            pass
    return chunks


# ---------------------------------------------------------------------------
# stream_reader — writes lines to disk, only to disk
# ---------------------------------------------------------------------------

def test_stream_reader_writes_lines_to_log_file(tmp_path):
    log = ProcessLogFile(str(tmp_path / "p.log"), max_bytes=10_000, backups=1)
    stream_reader(io.StringIO("a\nb\nc\n"), log)
    log.close()
    assert (tmp_path / "p.log").read_text() == "a\nb\nc\n"


def test_stream_reader_skips_blank_lines(tmp_path):
    log = ProcessLogFile(str(tmp_path / "p.log"), max_bytes=10_000, backups=1)
    stream_reader(io.StringIO("a\n\nb\n"), log)
    log.close()
    assert (tmp_path / "p.log").read_text() == "a\nb\n"


def test_stream_reader_handles_empty_stream(tmp_path):
    log = ProcessLogFile(str(tmp_path / "p.log"), max_bytes=10_000, backups=1)
    stream_reader(io.StringIO(""), log)
    log.close()
    assert (tmp_path / "p.log").read_text() == ""


def test_stream_reader_no_log_file_is_noop():
    """Disk logging may be disabled; reader must drain the pipe regardless."""
    stream_reader(io.StringIO("a\nb\n"), None)  # must not raise


def test_stream_reader_decodes_bytes():
    """Popen(text=True) returns str, but the reader must tolerate bytes too."""
    class ByteStream:
        def __init__(self, data: bytes):
            self._buf = io.BytesIO(data)
        def readline(self):
            return self._buf.readline()
    stream_reader(ByteStream(b"hi\n"), None)  # must not raise


# ---------------------------------------------------------------------------
# publish_procs_outputs — subscription gating
# ---------------------------------------------------------------------------

def test_no_subscribers_publishes_nothing(agent, tmp_path):
    agent.process_log_dir = str(tmp_path)
    log = _make_proc(agent, subscribe=False, log_path=str(tmp_path / "p1.log"))
    log.write("hello\n")
    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()


def test_first_cycle_anchors_at_eof_and_publishes_nothing(agent, tmp_path):
    """A fresh subscriber sees only content written *after* the subscribe."""
    agent.process_log_dir = str(tmp_path)
    log = _make_proc(agent, log_path=str(tmp_path / "p1.log"))
    log.write("pre-subscribe\n")
    publish_procs_outputs(agent)
    # First call records the offset; nothing shipped.
    agent.lc.publish.assert_not_called()
    assert "p1" in agent._log_offsets


def test_second_cycle_ships_new_lines(agent, tmp_path):
    agent.process_log_dir = str(tmp_path)
    log = _make_proc(agent, log_path=str(tmp_path / "p1.log"))
    publish_procs_outputs(agent)  # anchor
    log.write("hello world\n")
    publish_procs_outputs(agent)
    chunks = _published_chunks(agent)
    assert len(chunks) == 1
    assert chunks[0].content == "hello world\n"
    assert chunks[0].request_seq == 0
    assert chunks[0].name == "p1"


def test_expired_subscription_is_reaped(agent, tmp_path):
    import time
    agent.process_log_dir = str(tmp_path)
    log = _make_proc(agent, subscribe=False, log_path=str(tmp_path / "p1.log"))
    agent.output_subscriptions["p1"] = time.monotonic() - 1.0
    agent._log_offsets["p1"] = (0, 1)
    log.write("ignored\n")
    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()
    assert "p1" not in agent.output_subscriptions
    assert "p1" not in agent._log_offsets


# ---------------------------------------------------------------------------
# publish_procs_outputs — chunking, partial lines, rotation
# ---------------------------------------------------------------------------

def test_chunk_capped_at_max_output_chunk(agent, tmp_path):
    agent.process_log_dir = str(tmp_path)
    log = _make_proc(agent, log_path=str(tmp_path / "p1.log"))
    publish_procs_outputs(agent)  # anchor
    big_line = "x" * (MAX_OUTPUT_CHUNK * 2) + "\n"
    log.write(big_line)
    publish_procs_outputs(agent)
    chunks = _published_chunks(agent)
    assert len(chunks) == 1
    # Capped at MAX_OUTPUT_CHUNK; trimmed at last newline (none in this slice
    # because the only newline is well past MAX_OUTPUT_CHUNK), so this is the
    # degenerate "single line longer than the chunk" path: ship as-is.
    assert len(chunks[0].content) == MAX_OUTPUT_CHUNK


def test_partial_trailing_line_held_until_newline(agent, tmp_path):
    """A line still being written must not be shipped half-formed."""
    agent.process_log_dir = str(tmp_path)
    path = tmp_path / "p1.log"
    log = _make_proc(agent, log_path=str(path))
    publish_procs_outputs(agent)  # anchor

    # Bypass the line-discipline of ProcessLogFile.write_marker and append a
    # partial line directly to simulate a half-flushed write.
    with open(path, "a") as f:
        f.write("partial-without-newline")

    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()  # nothing shipped — no \n yet

    with open(path, "a") as f:
        f.write("-now-complete\n")
    publish_procs_outputs(agent)
    chunks = _published_chunks(agent)
    assert len(chunks) == 1
    assert chunks[0].content == "partial-without-newline-now-complete\n"


def test_remainder_after_chunk_is_shipped_next_cycle(agent, tmp_path):
    agent.process_log_dir = str(tmp_path)
    path = tmp_path / "p1.log"
    log = _make_proc(agent, log_path=str(path))
    publish_procs_outputs(agent)  # anchor

    # Write a chunk whose first newline-terminated slice fits in
    # MAX_OUTPUT_CHUNK and a tail that won't fit until the next cycle.
    line_size = MAX_OUTPUT_CHUNK - 100
    with open(path, "a") as f:
        f.write("a" * line_size + "\n")
        f.write("b" * 200 + "\n")

    publish_procs_outputs(agent)
    publish_procs_outputs(agent)
    chunks = _published_chunks(agent)
    body = "".join(c.content for c in chunks)
    assert body.count("\n") == 2


def test_rotation_resets_offset(agent, tmp_path):
    agent.process_log_dir = str(tmp_path)
    path = tmp_path / "p1.log"
    log = _make_proc(agent, log_path=str(path))
    publish_procs_outputs(agent)  # anchor

    log.write("before-rotation\n")
    publish_procs_outputs(agent)
    chunks_before = _published_chunks(agent)
    assert any("before-rotation" in c.content for c in chunks_before)

    # Force a rotation: rename current file aside, create a fresh one at
    # the same path. ProcessLogFile keeps writing to the original fd, so
    # we close and reopen it to mirror what _rotate_locked does.
    log.close()
    os.rename(str(path), str(path) + ".1")
    log2 = ProcessLogFile(str(path), max_bytes=10_000_000, backups=2)
    agent.processes["p1"].log_file = log2
    log2.write("after-rotation\n")

    agent.lc.publish.reset_mock()
    publish_procs_outputs(agent)
    chunks_after = _published_chunks(agent)
    assert any("after-rotation" in c.content for c in chunks_after)


def test_publish_failure_does_not_advance_offset(agent, tmp_path):
    """If LCM publish raises, the bytes stay in line for the next cycle."""
    agent.process_log_dir = str(tmp_path)
    log = _make_proc(agent, log_path=str(tmp_path / "p1.log"))
    publish_procs_outputs(agent)  # anchor
    log.write("retry-me\n")

    agent.lc.publish.side_effect = OSError("boom")
    publish_procs_outputs(agent)
    # Offset must not have advanced for the failed cycle.
    pre_offset, _ = agent._log_offsets["p1"]
    assert pre_offset == 0  # we anchored at empty file then failed publish

    # Recover and try again on the next cycle.
    agent.lc.publish.side_effect = None
    agent.lc.publish.reset_mock()
    publish_procs_outputs(agent)
    chunks = _published_chunks(agent)
    assert any("retry-me" in c.content for c in chunks)


def test_skips_processes_without_log_file(agent, tmp_path):
    """A process whose disk logging is disabled is silently skipped."""
    create_process(agent, "p1", "cmd", False, False, "")
    agent.processes["p1"].log_file = None
    agent.output_subscriptions["p1"] = float("inf")
    agent.lc.publish.reset_mock()
    publish_procs_outputs(agent)
    agent.lc.publish.assert_not_called()
