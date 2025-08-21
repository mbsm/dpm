from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QDialog, QTextEdit, QVBoxLayout
from PyQt5.QtGui import QTextCursor, QFont, QFontMetrics

class ProcessOutput(QDialog):
    def __init__(self, proc_name, initial_text="", controller=None, parent=None):
        super().__init__(parent)
        self.proc_name = proc_name
        self.controller = controller

        self.setWindowTitle(f"Output - {proc_name}")
        self.resize(800, 500)  # temporary; will be adjusted to 40 cols below

        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        # Monospace + no wrapping for predictable column width
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        self.text.setFont(mono)
        self.text.setLineWrapMode(QTextEdit.NoWrap)

        # Accumulated buffer and last-message key (to avoid duplicate appends)
        self._buffer = initial_text or ""
        self._last_key = None
        if self._buffer:
            self.text.setPlainText(self._buffer)

        lay = QVBoxLayout(self)
        lay.addWidget(self.text)

        # Set dialog width to ~40 characters
        try:
            fm = QFontMetrics(self.text.font())
            char_w = fm.horizontalAdvance("M")  # wide glyph as reference
            cols = 40
            # Account for editor frame, scrollbar, and layout margins
            frame = self.text.frameWidth() * 2
            sb_w = self.text.verticalScrollBar().sizeHint().width()
            margins = lay.contentsMargins()
            outer = margins.left() + margins.right()
            target_w = int(char_w * cols + frame + sb_w + outer + 8)  # small padding
            self.resize(target_w, self.height())
            self.setMinimumWidth(target_w)
        except Exception:
            pass

        self._timer = None
        if self.controller is not None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._refresh_from_controller)
            self._timer.start(500)  # ms

    def _refresh_from_controller(self):
        try:
            msg = getattr(self.controller, "proc_outputs", {}).get(self.proc_name)
        except Exception:
            msg = None

        if msg is None:
            return

        # Build payload including stdout and stderr (and any combined field)
        combined = (
            getattr(msg, "output", None)
            or getattr(msg, "text", None)
            or ""
        )
        stdout = getattr(msg, "stdout", None) or getattr(msg, "out", None) or ""
        stderr = getattr(msg, "stderr", None) or getattr(msg, "err", None) or ""

        parts = []
        if combined:
            parts.append(combined)
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append("[stderr]\n" + stderr)

        payload = "\n".join(p for p in parts if p)

        # Skip if nothing to add
        if not payload:
            return

        # Detect new message (best-effort: use seq/timestamp/utime + lengths)
        key = (
            getattr(msg, "seq", None),
            getattr(msg, "timestamp", None),
            getattr(msg, "utime", None),
            len(stdout),
            len(stderr),
            len(combined),
        )
        if key == self._last_key:
            return
        self._last_key = key

        # Compute text to append (ensure single newline separation)
        append_text = payload
        if self._buffer and not self._buffer.endswith("\n") and not payload.startswith("\n"):
            append_text = "\n" + payload

        # Append to buffer
        self._buffer += append_text

        # Preserve scroll-at-bottom behavior
        sb = self.text.verticalScrollBar()
        at_bottom = sb.value() == sb.maximum()

        # Rolling buffer limit to avoid unbounded growth
        MAX_BUFFER_BYTES = 2 * 1024 * 1024  # ~2 MB
        if len(self._buffer) > MAX_BUFFER_BYTES:
            self._buffer = self._buffer[-MAX_BUFFER_BYTES:]
            self.text.setPlainText(self._buffer)
        else:
            # Insert only the new text efficiently
            cursor = self.text.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(append_text)
            self.text.setTextCursor(cursor)

        if at_bottom:
            sb.setValue(sb.maximum())

    def closeEvent(self, event):
        if self._timer is not None:
            self._timer.stop()
        super().closeEvent(event)