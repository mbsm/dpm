"""Shared configuration loading for DPM agent and supervisor."""

import os
from typing import List

import yaml


def load_dpm_config(config_path: str, required_fields: List[str]) -> dict:
    """Load and validate a DPM YAML config file.

    Raises FileNotFoundError, PermissionError, ValueError, or KeyError
    on invalid input.
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Configuration file {config_path} not found.")
    if not os.access(config_path, os.R_OK):
        raise PermissionError(f"Configuration file {config_path} is not readable.")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(
            f"Error parsing YAML configuration file {config_path}: {e}"
        ) from e
    except OSError as e:
        raise RuntimeError(
            f"Unexpected error loading configuration file {config_path}: {e}"
        ) from e

    for field in required_fields:
        if field not in config:
            raise KeyError(f"Missing required configuration field: {field}")

    return config
