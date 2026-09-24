"""Entry point: python -m esp32scan.gui [--port P] [--mode wifi|bt|both] [--no-record]"""

import argparse
import sys

import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

from .. import DEFAULT_DB
from ..link import find_port
from . import theme
from .window import MainWindow


def main():
    ap = argparse.ArgumentParser(description="ESP32 WiFi + Bluetooth scanner GUI")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--mode", choices=["wifi", "bt", "both", "fast"], default="both")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite capture log")
    ap.add_argument("--no-record", action="store_true", help="don't log scans to the db")
    args = ap.parse_args()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ESP32 Scanner")
    theme.apply(app)
    pg.setConfigOptions(antialias=True, foreground=theme.MUTED)

    win = MainWindow(port=args.port or find_port(), mode=args.mode,
                     db_path=args.db, record=not args.no_record)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
