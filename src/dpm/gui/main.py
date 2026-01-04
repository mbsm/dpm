import os
import sys

from PyQt5.QtWidgets import QApplication, QMessageBox

from dpm.controller.controller import Controller  # type: ignore
from dpm.gui.main_window import MainWindow  # type: ignore


def main() -> None:
    app = QApplication(sys.argv)

    config_path = os.environ.get("DPM_CONFIG", "/etc/dpm/dpm.yaml")

    try:
        controller = Controller(config_path)
        controller.start()
    except Exception as e:
        QMessageBox.critical(
            None,
            "DPM GUI startup error",
            f"Failed to start DPM GUI.\n\nConfig: {config_path}\n\nError: {e}",
        )
        sys.exit(1)

    window = MainWindow(controller)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
