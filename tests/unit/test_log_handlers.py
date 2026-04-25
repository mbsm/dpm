"""Tests for the daemon's read_log + subscribe_output command handlers."""

from __future__ import annotations

import time

import pytest

from dpm.constants import DPM_PROTOCOL_VERSION
from dpm_msgs import command_t, log_chunk_t


def _cmd(action, name="p1", since_us=0, tail_lines=0, ttl_seconds=0, seq=42):
    msg = command_t()
    msg.protocol_version = DPM_PROTOCOL_VERSION
    msg.seq = seq
    msg.action = action
    msg.name = name
    msg.group = ""
    msg.hostname = ""  # broadcast
    msg.since_us = since_us
    msg.tail_lines = tail_lines
    msg.ttl_seconds = ttl_seconds
    return msg


def _published_chunks(agent):
    """Pull every log_chunk_t the daemon published since the last reset."""
    chunks = []
    for call in agent.lc.publish.call_args_list:
        _channel, encoded = call.args
        try:
            chunks.append(log_chunk_t.decode(encoded))
        except Exception:
            pass
    return chunks


# ---------------------------------------------------------------------------
# read_log
# ---------------------------------------------------------------------------

def test_read_log_publishes_disk_content(agent, tmp_path):
    from dpmd.commands import command_handler

    agent.process_log_dir = str(tmp_path)
    (tmp_path / "p1.log").write_text("first\nsecond\nthird\n")

    agent.lc.publish.reset_mock()
    command_handler(agent, "ch", _cmd("read_log", name="p1").encode())

    chunks = _published_chunks(agent)
    assert len(chunks) >= 1
    body = "".join(c.content for c in chunks)
    assert "first" in body and "third" in body
    # Last chunk must be marked as such, and request_seq echoes the request.
    assert chunks[-1].last is True
    assert chunks[-1].request_seq == 42
    assert chunks[-1].name == "p1"


def test_read_log_with_tail_lines_caps_output(agent, tmp_path):
    from dpmd.commands import command_handler

    agent.process_log_dir = str(tmp_path)
    lines = "\n".join(f"line{i}" for i in range(50)) + "\n"
    (tmp_path / "p1.log").write_text(lines)

    agent.lc.publish.reset_mock()
    command_handler(agent, "ch", _cmd("read_log", name="p1", tail_lines=3).encode())

    body = "".join(c.content for c in _published_chunks(agent))
    assert body.splitlines() == ["line47", "line48", "line49"]


def test_read_log_returns_empty_chunk_when_no_log(agent, tmp_path):
    """Even with no on-disk log, the daemon emits one final chunk (last=True)."""
    from dpmd.commands import command_handler

    agent.process_log_dir = str(tmp_path)  # empty dir
    agent.lc.publish.reset_mock()
    command_handler(agent, "ch", _cmd("read_log", name="missing").encode())

    chunks = _published_chunks(agent)
    assert len(chunks) == 1
    assert chunks[0].last is True
    assert chunks[0].content == ""


def test_read_log_walks_rotated_files(agent, tmp_path):
    from dpmd.commands import command_handler

    agent.process_log_dir = str(tmp_path)
    (tmp_path / "p1.log.2").write_text("oldest\n")
    (tmp_path / "p1.log.1").write_text("middle\n")
    (tmp_path / "p1.log").write_text("newest\n")

    agent.lc.publish.reset_mock()
    command_handler(agent, "ch", _cmd("read_log", name="p1").encode())

    body = "".join(c.content for c in _published_chunks(agent))
    # Order: oldest -> middle -> newest
    assert body == "oldest\nmiddle\nnewest\n"


# ---------------------------------------------------------------------------
# subscribe_output
# ---------------------------------------------------------------------------

def test_subscribe_output_records_active_subscription(agent):
    from dpmd.commands import command_handler

    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=3).encode())
    assert "p1" in agent.output_subscriptions
    expires_at = agent.output_subscriptions["p1"]
    assert expires_at > time.monotonic()
    # TTL should be respected (≤ requested + small slack)
    assert expires_at - time.monotonic() <= 3.5


def test_subscribe_output_zero_ttl_uses_default(agent):
    from dpmd.commands import command_handler

    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=0).encode())
    expires_at = agent.output_subscriptions["p1"]
    # Default is 5 s
    delta = expires_at - time.monotonic()
    assert 4.0 <= delta <= 5.5


def test_subscribe_output_clamps_huge_ttl(agent):
    from dpmd.commands import command_handler

    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=999999).encode())
    expires_at = agent.output_subscriptions["p1"]
    delta = expires_at - time.monotonic()
    assert delta <= 60.5  # _MAX_SUBSCRIPTION_TTL == 60.0


def test_subscribe_output_extends_existing_subscription(agent):
    from dpmd.commands import command_handler

    # First subscribe with short TTL
    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=1, seq=1).encode())
    first = agent.output_subscriptions["p1"]
    time.sleep(0.05)
    # Renew with longer TTL
    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=10, seq=2).encode())
    second = agent.output_subscriptions["p1"]
    assert second > first


def test_first_subscribe_clears_tail_offset(agent):
    """A fresh subscribe drops any stale tail offset so the next publish
    cycle re-anchors at current EOF — a client that has just seeded its
    view via read_log won't see those bytes re-published on follow."""
    from dpmd.commands import command_handler
    from dpmd.processes import create_process

    create_process(agent, "p1", "cmd", False, False, "")
    # Plant stale per-process tail state.
    agent._log_offsets["p1"] = (12345, 99)
    agent._live_chunk_index["p1"] = 7

    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=5).encode())

    assert "p1" not in agent._log_offsets
    assert "p1" not in agent._live_chunk_index


def test_subscribe_renewal_preserves_tail_offset(agent):
    """Renewing an active subscription must NOT reset the offset."""
    from dpmd.commands import command_handler
    from dpmd.processes import create_process

    create_process(agent, "p1", "cmd", False, False, "")
    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=10, seq=1).encode())

    agent._log_offsets["p1"] = (4242, 17)
    agent._live_chunk_index["p1"] = 3

    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=10, seq=2).encode())

    assert agent._log_offsets["p1"] == (4242, 17)
    assert agent._live_chunk_index["p1"] == 3


def test_subscribe_after_expiry_clears_offset_again(agent):
    """An expired subscription is treated as fresh: re-subscribe resets."""
    import time as _time
    from dpmd.commands import command_handler
    from dpmd.processes import create_process

    create_process(agent, "p1", "cmd", False, False, "")
    agent.output_subscriptions["p1"] = _time.monotonic() - 1.0
    agent._log_offsets["p1"] = (999, 5)

    command_handler(agent, "ch", _cmd("subscribe_output", name="p1", ttl_seconds=5).encode())

    assert "p1" not in agent._log_offsets
