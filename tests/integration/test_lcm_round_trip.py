"""End-to-end LCM round-trip between a real ``dpmd`` subprocess and a Client.

This is the one test in the suite that exercises the actual wire: it
subprocess-launches ``dpmd`` on a loopback multicast group, creates a
Client pointing to the same URL, and verifies command → telemetry
round-trips for the lifecycle (create → start → stop → delete).

Tagged ``integration`` so CI can opt in. Skipped automatically if
``dpmd`` can't be invoked or a multicast socket can't be opened.
"""

import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

_DPMD_BIN = shutil.which("dpmd") or str(Path(sys.executable).parent / "dpmd")
if not os.path.exists(_DPMD_BIN):
    pytest.skip("dpmd binary not found — run 'pip install -e .'", allow_module_level=True)


def _check_multicast_supported() -> bool:
    """Return True if we can open a UDP socket with multicast options set."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
            return True
        finally:
            s.close()
    except OSError:
        return False


if not _check_multicast_supported():
    pytest.skip("Multicast not supported on this host", allow_module_level=True)


def _unique_lcm_url() -> str:
    """Build a unique loopback-only multicast URL to isolate this test run."""
    a = random.randint(1, 254)
    b = random.randint(1, 254)
    c = random.randint(1, 254)
    port = random.randint(20000, 60000)
    # ttl=0 → packets never leave this host
    return f"udpm://239.{a}.{b}.{c}:{port}?ttl=0"


@pytest.fixture
def daemon_cfg(tmp_path) -> str:
    """Write a minimal dpmd config with short intervals and return its path."""
    cfg = {
        "lcm_url": _unique_lcm_url(),
        "command_channel": "ITEST/commands",
        "host_info_channel": "ITEST/host_info",
        "proc_outputs_channel": "ITEST/proc_outputs",
        "host_procs_channel": "ITEST/host_procs",
        "stop_timeout": 2,
        "monitor_interval": 0.2,
        "output_interval": 0.2,
        "host_status_interval": 0.2,
        "procs_status_interval": 0.2,
        "stop_signal": "SIGINT",
        "max_restarts": -1,
        # Persist to tmp so the test doesn't write to /var/lib/dpm
        "persist_path": str(tmp_path / "processes.yaml"),
    }
    path = tmp_path / "dpm_it.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


@pytest.fixture
def daemon_proc(daemon_cfg, tmp_path):
    """Spawn ``dpmd`` as a subprocess against the loopback LCM URL."""
    log_file = tmp_path / "dpmd.log"
    env = dict(os.environ)
    env["DPM_CONFIG"] = daemon_cfg
    env["DPM_LOG_LEVEL"] = "WARNING"  # quiet unless something breaks
    # PYTHONPATH so an un-installed checkout still finds dpm_msgs
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")

    with open(log_file, "w") as lf:
        proc = subprocess.Popen(
            [_DPMD_BIN],
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    # Give dpmd a beat to open its LCM socket
    time.sleep(0.5)

    # If it died immediately, surface the log
    if proc.poll() is not None:
        log_content = log_file.read_text()
        pytest.fail(f"dpmd exited before test started (rc={proc.returncode}):\n{log_content}")

    yield proc

    # Teardown: graceful stop, then escalate
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        proc.wait(timeout=2.0)


@pytest.fixture
def client_to_daemon(daemon_cfg, daemon_proc):
    """Client attached to the same LCM URL as the subprocess daemon."""
    from dpm.client import Client

    c = Client(config_path=daemon_cfg)
    c.start()
    _wait_for(lambda: bool(c.hosts), timeout=8.0,
              fail_msg="no host telemetry arrived — daemon or LCM wire is wrong")
    yield c
    c.stop()


def _wait_for(predicate, *, timeout: float, fail_msg: str = "condition not met"):
    """Poll *predicate* up to *timeout* seconds; raise AssertionError on timeout."""
    deadline = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < deadline:
        try:
            last_state = predicate()
            if last_state:
                return last_state
        except Exception:
            pass
        time.sleep(0.1)
    raise AssertionError(f"{fail_msg} (last={last_state})")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_host_telemetry_reaches_client(client_to_daemon):
    """Client receives host_info telemetry from the subprocess daemon."""
    # Fixture already waited for at least one host
    hosts = client_to_daemon.hosts
    assert len(hosts) == 1
    host = next(iter(hosts.values()))
    assert host.cpus > 0
    assert host.ip  # non-empty IP string


def test_create_start_stop_delete_round_trip(client_to_daemon):
    """Full lifecycle: Client sends commands over LCM, state confirmed via telemetry."""
    hostname = next(iter(client_to_daemon.hosts.keys()))
    name = "it_roundtrip"

    # 1. Create (daemon should register the process)
    client_to_daemon.create_proc(
        name, "sleep 100", "integration", hostname,
        auto_restart=False, realtime=False,
    )
    info = _wait_for(
        lambda: client_to_daemon.procs.get((hostname, name)),
        timeout=4.0, fail_msg="create telemetry didn't arrive",
    )
    # Newly created process is Ready
    assert info.state == "T"

    # 2. Start
    client_to_daemon.start_proc(name, hostname)
    _wait_for(
        lambda: (p := client_to_daemon.procs.get((hostname, name))) is not None
                and p.state == "R",
        timeout=4.0, fail_msg="process didn't reach Running",
    )

    # 3. Stop
    client_to_daemon.stop_proc(name, hostname)
    _wait_for(
        lambda: (p := client_to_daemon.procs.get((hostname, name))) is not None
                and p.state != "R",
        timeout=4.0, fail_msg="process didn't leave Running",
    )

    # 4. Delete
    client_to_daemon.del_proc(name, hostname)
    _wait_for(
        lambda: (hostname, name) not in client_to_daemon.procs,
        timeout=4.0, fail_msg="process didn't disappear after delete",
    )


def test_process_output_round_trip(client_to_daemon):
    """stdout of a running process reaches the Client's output buffer via LCM."""
    hostname = next(iter(client_to_daemon.hosts.keys()))
    name = "it_output"

    client_to_daemon.create_proc(
        name, "bash -c 'echo integration-ok; sleep 2'", "integration", hostname,
    )
    _wait_for(
        lambda: (hostname, name) in client_to_daemon.procs,
        timeout=4.0, fail_msg="create telemetry didn't arrive",
    )
    client_to_daemon.start_proc(name, hostname)

    # Wait for the stdout chunk to land in the Client's buffer
    def _has_output():
        _gen, text, _reset, _cur_len = client_to_daemon.get_proc_output_delta(
            name, last_gen=-1, last_len=0
        )
        return "integration-ok" in text

    _wait_for(_has_output, timeout=5.0, fail_msg="stdout didn't reach client")

    # Clean up
    client_to_daemon.stop_proc(name, hostname)
    client_to_daemon.del_proc(name, hostname)
