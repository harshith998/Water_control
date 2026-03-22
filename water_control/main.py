"""
Water Gate Control System — entry point.

Usage:
    python main.py              # uses config.yaml in current directory
    python main.py my_plant.yaml
"""

import sys
import os
import yaml

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from ui.dashboard import Dashboard


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    config = load_config(config_path)

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    window = Dashboard(config)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    # Run from the water_control directory so relative imports work
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
