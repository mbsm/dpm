"""Polling helpers for waiting on LCM telemetry in the CLI."""

import time


def wait_for_telemetry(client, timeout: float = 6.0, settle: float = 2.0, poll: float = 0.1) -> bool:
    """Block until telemetry arrives, then wait for additional hosts to check in.

    Waits up to `timeout` seconds for the first host to appear, then waits
    an additional `settle` seconds to collect broadcasts from other hosts.
    Returns True if at least one host was seen.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.hosts:
            # First host seen — wait a bit longer for others to report in
            time.sleep(settle)
            return True
        time.sleep(poll)
    return False


def wait_for_state(
    client, name: str, host: str,
    target: str = None,
    not_target: str = None,
    timeout: float = 3.0,
    poll: float = 0.2,
) -> bool:
    """Block until the process reaches (or leaves) a state, or timeout.

    Pass target="R" to wait until state == "R".
    Pass not_target="R" to wait until state != "R".
    Returns True if condition met within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        procs = client.procs
        info = procs.get((host, name))
        if info is not None:
            state = getattr(info, "state", "")
            if target and state == target:
                return True
            if not_target and state != not_target:
                return True
        time.sleep(poll)
    return False


def wait_for_proc_gone(
    client, name: str, host: str,
    timeout: float = 3.0,
    poll: float = 0.2,
) -> bool:
    """Block until the process disappears from the client's procs dict."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (host, name) not in client.procs:
            return True
        time.sleep(poll)
    return False
