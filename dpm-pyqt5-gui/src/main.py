import sys
import os
from PyQt5.QtWidgets import QApplication

# Add the master directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../master')))
from master import DPM_Master

from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    master = DPM_Master("/home/mbustos/dpm/dpm.yaml")
    master.start()
    window = MainWindow(master)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()