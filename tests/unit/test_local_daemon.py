"""Tests for GUI's local-daemon spawn/stop helpers."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_global():
    """Clear the module-level handle between tests."""
    from dpm.gui import local_daemon
    local_daemon._last_spawned_proc = None
    yield
    local_daemon._last_spawned_proc = None


def test_first_spawn_records_handle():
    from dpm.gui import local_daemon

    fake = MagicMock()
    fake.pid = 4242
    fake.poll.return_value = None  # alive

    with patch("dpm.gui.local_daemon.subprocess.Popen", return_value=fake) as mock_popen:
        pid, _log = local_daemon.spawn_local_daemon()

    assert pid == 4242
    assert mock_popen.call_count == 1
    assert local_daemon._last_spawned_proc is fake


def test_second_spawn_while_alive_is_rejected():
    from dpm.gui import local_daemon

    fake = MagicMock()
    fake.pid = 4242
    fake.poll.return_value = None  # alive

    with patch("dpm.gui.local_daemon.subprocess.Popen", return_value=fake):
        local_daemon.spawn_local_daemon()

    # Second spawn should raise without calling Popen again
    with patch("dpm.gui.local_daemon.subprocess.Popen") as mock_popen:
        with pytest.raises(RuntimeError, match="already running"):
            local_daemon.spawn_local_daemon()
        mock_popen.assert_not_called()


def test_second_spawn_after_first_exits_is_allowed():
    from dpm.gui import local_daemon

    dead = MagicMock()
    dead.pid = 1
    dead.poll.return_value = 0  # exited

    new = MagicMock()
    new.pid = 2
    new.poll.return_value = None

    local_daemon._last_spawned_proc = dead

    with patch("dpm.gui.local_daemon.subprocess.Popen", return_value=new) as mock_popen:
        pid, _log = local_daemon.spawn_local_daemon()

    assert pid == 2
    assert mock_popen.call_count == 1
