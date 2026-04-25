"""Tests for Client.get_proc_output_delta — generation and bounds logic."""

import pytest


def _inject(client, name, text, gen=0):
    """Directly set the internal buffer state, bypassing the handler."""
    from dpm.client import _ProcOutputState
    with client._outputs_lock:
        client._proc_output_states[name] = _ProcOutputState(buf=text, gen=gen)


# ---------------------------------------------------------------------------
# Normal append path
# ---------------------------------------------------------------------------

def test_delta_first_call_returns_full_buffer(client):
    _inject(client, "p1", "hello world")
    gen, text, reset, length = client.get_proc_output_delta("p1", 0, 0)
    assert reset is False
    assert text == "hello world"
    assert length == len("hello world")
    assert gen == 0


def test_delta_returns_only_new_suffix(client):
    _inject(client, "p1", "hello world")
    # Caller already saw first 5 chars ("hello")
    gen, text, reset, length = client.get_proc_output_delta("p1", 0, 5)
    assert reset is False
    assert text == " world"
    assert length == 11


def test_delta_at_end_returns_empty_string(client):
    _inject(client, "p1", "hello")
    gen, text, reset, length = client.get_proc_output_delta("p1", 0, 5)
    assert reset is False
    assert text == ""
    assert length == 5


# ---------------------------------------------------------------------------
# Reset triggers
# ---------------------------------------------------------------------------

def test_delta_generation_mismatch_triggers_reset(client):
    _inject(client, "p1", "new content", gen=3)
    # Caller last saw gen=0 (buffer was trimmed twice since then)
    gen, text, reset, length = client.get_proc_output_delta("p1", 0, 100)
    assert reset is True
    assert text == "new content"
    assert gen == 3


def test_delta_out_of_bounds_last_len_triggers_reset(client):
    _inject(client, "p1", "short", gen=0)
    # Caller thinks it saw 100 chars but buffer is only 5
    gen, text, reset, length = client.get_proc_output_delta("p1", 0, 100)
    assert reset is True
    assert text == "short"
    assert length == 5


# ---------------------------------------------------------------------------
# Unknown process
# ---------------------------------------------------------------------------

def test_delta_unknown_process_returns_empty(client):
    gen, text, reset, length = client.get_proc_output_delta("no_such_proc", 0, 0)
    assert reset is False
    assert text == ""
    assert length == 0
    assert gen == 0


# ---------------------------------------------------------------------------
# Generation increment via real handler
# ---------------------------------------------------------------------------

def test_trim_via_handler_increments_generation(client):
    from dpm.constants import DPM_PROTOCOL_VERSION
    from dpm_msgs import log_chunk_t

    MAX_BYTES = 2 * 1024 * 1024
    msg = log_chunk_t()
    msg.protocol_version = DPM_PROTOCOL_VERSION
    msg.request_seq = 0  # live publish
    msg.timestamp = 0
    msg.hostname = "h1"
    msg.name = "p1"
    msg.chunk_index = 0
    msg.last = False
    msg.content = "x" * (MAX_BYTES + 1000)
    client.log_chunks_handler(None, msg.encode())

    gen, _, reset, _ = client.get_proc_output_delta("p1", 0, 0)
    assert gen >= 1
    assert reset is True  # gen mismatch from caller's perspective (last_gen=0)


def test_no_trim_keeps_generation_zero(client):
    _inject(client, "p1", "small text", gen=0)
    gen, _, reset, _ = client.get_proc_output_delta("p1", 0, 0)
    assert gen == 0
    assert reset is False
