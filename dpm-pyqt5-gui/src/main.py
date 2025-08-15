import sys
import os
from PyQt5.QtWidgets import QApplication

# Add the master directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../master')))
from master import DPM_Master

# Add the controller directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../controller')))
from controller import Controller

from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    controller = Controller("/home/mbustos/dpm/dpm.yaml")
    controller.start()
    window = MainWindow(controller)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()