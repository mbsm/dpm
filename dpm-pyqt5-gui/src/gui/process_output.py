from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton

class ProcessOutput(QDialog):
    def __init__(self, proc_name, output, parent=None):
        super(ProcessOutput, self).__init__(parent)
        self.setWindowTitle(f"Output for {proc_name}")
        self.setGeometry(100, 100, 600, 400)

        layout = QVBoxLayout()

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setPlainText(output)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        layout.addWidget(QLabel(f"Output for {proc_name}:"))
        layout.addWidget(self.output_text)
        layout.addWidget(close_button)

        self.setLayout(layout)