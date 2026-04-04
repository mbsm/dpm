from __future__ import annotations

"""Output window that streams a process log buffer."""

import logging

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import QDialog, QTextEdit, QVBoxLayout


class ProcessOutput(QDialog):
    def __init__(
        self, proc_name: str, initial_text: str = "", initial_gen: int = 0, controller=None, parent=None
    ):
        super().__init__(parent)
        self.proc_name = proc_name
        self.controller = controller

        self.setWindowTitle(f"Output: {proc_name}")

        layout = QVBoxLayout(self)
        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Monospace"))
        layout.addWidget(self.text)

        # State for delta fetch (avoids big copies)
        self._last_gen = -1
        self._last_len = 0

        if initial_text:
            self.text.setPlainText(initial_text)
            self._last_gen = initial_gen
            self._last_len = len(initial_text)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_controller)
        self._timer.start(500)

    def _refresh_from_controller(self) -> None:
        if self.controller is None:
            return

        if not hasattr(self.controller, "get_proc_output_delta"):
            # Fallback: avoid crashing if controller is older; do nothing.
            return

        gen, delta, reset, cur_len = self.controller.get_proc_output_delta(
            self.proc_name,
            self._last_gen,
            self._last_len,
        )

        if not delta and not reset:
            return

        if reset:
            self.text.setPlainText(delta)
        else:
            self.text.moveCursor(QTextCursor.End)
            self.text.insertPlainText(delta)

        self._last_gen = gen
        self._last_len = cur_len

        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, event):
        try:
            self._timer.stop()
        except (RuntimeError, AttributeError) as e:
            logging.debug("ProcessOutput timer stop failed: %s", e)
        super().closeEvent(event)
