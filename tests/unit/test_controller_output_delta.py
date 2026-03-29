"""Tests for Controller.get_proc_output_delta — generation and bounds logic."""

import pytest


def _inject(controller, name, text, gen=0):
    """Directly set the internal buffer state, bypassing the handler."""
    with controller._outputs_lock:
        controller._proc_output_buffers[name] = text
        controller._proc_output_buffer_gen[name] = gen


# ---------------------------------------------------------------------------
# Normal append path
# ---------------------------------------------------------------------------

def test_delta_first_call_returns_full_buffer(controller):
    _inject(controller, "p1", "hello world")
    gen, text, reset, length = controller.get_proc_output_delta("p1", 0, 0)
    assert reset is False
    assert text == "hello world"
    assert length == len("hello world")
    assert gen == 0


def test_delta_returns_only_new_suffix(controller):
    _inject(controller, "p1", "hello world")
    # Caller already saw first 5 chars ("hello")
    gen, text, reset, length = controller.get_proc_output_delta("p1", 0, 5)
    assert reset is False
    assert text == " world"
    assert length == 11


def test_delta_at_end_returns_empty_string(controller):
    _inject(controller, "p1", "hello")
    gen, text, reset, length = controller.get_proc_output_delta("p1", 0, 5)
    assert reset is False
    assert text == ""
    assert length == 5


# ---------------------------------------------------------------------------
# Reset triggers
# ---------------------------------------------------------------------------

def test_delta_generation_mismatch_triggers_reset(controller):
    _inject(controller, "p1", "new content", gen=3)
    # Caller last saw gen=0 (buffer was trimmed twice since then)
    gen, text, reset, length = controller.get_proc_output_delta("p1", 0, 100)
    assert reset is True
    assert text == "new content"
    assert gen == 3


def test_delta_out_of_bounds_last_len_triggers_reset(controller):
    _inject(controller, "p1", "short", gen=0)
    # Caller thinks it saw 100 chars but buffer is only 5
    gen, text, reset, length = controller.get_proc_output_delta("p1", 0, 100)
    assert reset is True
    assert text == "short"
    assert length == 5


# ---------------------------------------------------------------------------
# Unknown process
# ---------------------------------------------------------------------------

def test_delta_unknown_process_returns_empty(controller):
    gen, text, reset, length = controller.get_proc_output_delta("no_such_proc", 0, 0)
    assert reset is False
    assert text == ""
    assert length == 0
    assert gen == 0


# ---------------------------------------------------------------------------
# Generation increment via real handler
# ---------------------------------------------------------------------------

def test_trim_via_handler_increments_generation(controller):
    from dpm_msgs import proc_output_t

    MAX_BYTES = 2 * 1024 * 1024
    msg = proc_output_t()
    msg.timestamp = 0
    msg.name = "p1"
    msg.hostname = "h1"
    msg.group = ""
    msg.stdout = "x" * (MAX_BYTES + 1000)
    msg.stderr = ""
    controller.proc_outputs_handler(None, msg.encode())

    gen, _, reset, _ = controller.get_proc_output_delta("p1", 0, 0)
    assert gen >= 1
    assert reset is True  # gen mismatch from caller's perspective (last_gen=0)


def test_no_trim_keeps_generation_zero(controller):
    _inject(controller, "p1", "small text", gen=0)
    gen, _, reset, _ = controller.get_proc_output_delta("p1", 0, 0)
    assert gen == 0
    assert reset is False
