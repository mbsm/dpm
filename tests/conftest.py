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
def node_agent(config_path):
    """NodeAgent with mocked LCM — safe for unit tests (no network)."""
    with patch("dpm.node.node.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        from dpm.node.node import NodeAgent
        agent = NodeAgent(config_file=config_path)
        yield agent


@pytest.fixture
def controller(config_path):
    """Controller with mocked LCM — safe for unit tests (no network)."""
    with patch("dpm.controller.controller.lcm.LCM") as MockLCM:
        MockLCM.return_value = MagicMock()
        from dpm.controller.controller import Controller
        ctrl = Controller(config_path)
        yield ctrl
