import os
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

# Import the application bits
from dpm.controller.controller import Controller  # type: ignore
from dpm.gui.main_window import MainWindow  # type: ignore


def _default_config_path() -> str:
    # Prefer XDG config, fall back to repo dpm.yaml for convenience
    xdg = os.getenv("XDG_CONFIG_HOME", os.path.join(Path.home(), ".config"))
    cfg = os.path.join(xdg, "dpm", "dpm.yaml")
    if os.path.isfile(cfg):
        return cfg
    # repo root fallback: search upwards for dpm.yaml relative to this file
    repo_root = Path(__file__).resolve().parents[3]
    fallback = repo_root / "dpm.yaml"
    return str(fallback)


def main() -> None:
    # Allow running even if called outside the repo root
    app = QApplication(sys.argv)

    config_path = _default_config_path()

    controller = Controller(config_path)
    controller.start()

    window = MainWindow(controller)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
