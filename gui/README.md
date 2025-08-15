# DPM PyQt5 GUI

A Qt-based GUI for DPM that connects to the Master backend and displays hosts and processes.

Entry point: [gui/src/main.py](src/main.py)

```python
# Excerpt
from PyQt5.QtWidgets import QApplication
from master import DPM_Master

def main():
    app = QApplication(sys.argv)
    master = DPM_Master("/home/mbustos/dpm/dpm.yaml")  # Ensure this path is correct
    master.start()
    window = MainWindow(master)
    window.show()
    sys.exit(app.exec_())
```

## Run
```bash
python3 gui/src/main.py
```

## Dependencies
- PyQt5, PyYAML, python3-lcm, psutil
- Install:
```bash
pip install PyQt5 pyyaml psutil
sudo apt-get install -y lcm liblcm-dev python3-lcm
# Optional GTK warning fix:
sudo apt-get install -y libcanberra-gtk-module libcanberra-gtk3-module
```

## Troubleshooting
- TypeError: MainWindow.__init__() missing 1 required positional argument: 'dpm_master'
  - Pass the DPM_Master instance to MainWindow as shown above.
- FileNotFoundError for config:
  - Update the DPM_Master config path to your actual /home/mbustos/dpm/dpm.yaml.