"""Shared fixtures for the DPM test suite."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "dpm.yaml"


@pytest.fixture(scope="session")
def config_path():
    return str(CONFIG_PATH)


@pytest.fixture
def agent(config_path):
    """Daemon with mocked LCM — safe for unit tests (no network)."""
    with patch("dpmd.daemon.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        from dpmd.daemon import Daemon
        a = Daemon(config_file=config_path)
        yield a


@pytest.fixture
def client(config_path):
    """Client with mocked LCM — safe for unit tests (no network)."""
    with patch("dpm.client.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        from dpm.client import Client
        s = Client(config_path)
        yield s
