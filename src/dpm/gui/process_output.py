from __future__ import annotations

"""Output window that streams a process log buffer."""

import logging

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout


class ProcessOutput(QDialog):
    def __init__(
        self, proc_name: str, initial_text: str = "", initial_gen: int = 0, client=None, parent=None
    ):
        super().__init__(parent)
        self.proc_name = proc_name
        self.client = client

        self.setWindowTitle(f"Output: {proc_name}")

        layout = QVBoxLayout(self)
        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Monospace"))
        layout.addWidget(self.text)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear_output)
        btn_layout.addWidget(self.clear_button)
        layout.addLayout(btn_layout)

        # State for delta fetch (avoids big copies)
        self._last_gen = -1
        self._last_len = 0

        if initial_text:
            self.text.setPlainText(initial_text)
            self._last_gen = initial_gen
            self._last_len = len(initial_text)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_client)
        self._timer.start(500)

    def _clear_output(self) -> None:
        self.text.clear()
        if self.client is not None and hasattr(self.client, "clear_proc_output"):
            self.client.clear_proc_output(self.proc_name)
        self._last_gen = -1
        self._last_len = 0

    def _refresh_from_client(self) -> None:
        if self.client is None:
            return

        if not hasattr(self.client, "get_proc_output_delta"):
            # Fallback: avoid crashing if client is older; do nothing.
            return

        gen, delta, reset, cur_len = self.client.get_proc_output_delta(
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
