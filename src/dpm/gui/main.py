import os
import sys


def main() -> None:
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print(
            "dpm-gui: error: PyQt5 is required but not installed.\n"
            "Install it with: sudo apt install python3-pyqt5",
            file=sys.stderr,
        )
        sys.exit(1)

    from dpm.client import Client
    from dpm.gui.main_window import MainWindow

    app = QApplication(sys.argv)

    config_path = os.environ.get("DPM_CONFIG", "/etc/dpm/dpm.yaml")

    try:
        client = Client(config_path)
        client.start()
    except Exception as e:
        QMessageBox.critical(
            None,
            "DPM GUI startup error",
            f"Failed to start DPM GUI.\n\nConfig: {config_path}\n\nError: {e}",
        )
        sys.exit(1)

    window = MainWindow(client)
    window.show()
    sys.exit(app.exec_())  # closeEvent handles client.stop()


if __name__ == "__main__":
    main()
