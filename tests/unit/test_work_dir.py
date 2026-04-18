"""Tests for per-process working directory."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CONFIG_PATH = Path(__file__).parent.parent.parent / "dpm.yaml"


def test_create_stores_work_dir(agent):
    agent.create_process("test", "echo hi", False, False, "grp",
                         work_dir="/tmp")
    assert agent.processes["test"].work_dir == "/tmp"


def test_create_default_work_dir(agent):
    agent.create_process("test", "echo hi", False, False, "grp")
    assert agent.processes["test"].work_dir == ""


def test_start_with_valid_work_dir(agent, tmp_path):
    work_dir = str(tmp_path)
    agent.create_process("test", "echo hi", False, False, "grp",
                         work_dir=work_dir)

    with patch("dpmd.daemon.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        agent.start_process("test")

        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == work_dir


def test_start_with_invalid_work_dir(agent):
    from dpm.constants import STATE_FAILED
    agent.create_process("test", "echo hi", False, False, "grp",
                         work_dir="/nonexistent/path/xyz")
    agent.start_process("test")
    assert agent.processes["test"].state == STATE_FAILED
    assert "does not exist" in agent.processes["test"].errors


def test_start_without_work_dir_no_cwd(agent):
    agent.create_process("test", "echo hi", False, False, "grp")

    with patch("dpmd.daemon.psutil.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 123
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        agent.start_process("test")

        _, kwargs = mock_popen.call_args
        assert "cwd" not in kwargs


def test_client_forwards_work_dir(client):
    client.create_proc("test", "echo hi", "grp", "host1",
                           work_dir="/opt/robot")
    assert client.lc_pub.publish.called
