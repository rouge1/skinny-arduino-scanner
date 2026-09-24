"""Entry point: python -m esp32scan.gui [--port P] [--mode wifi|bt|both] [--no-record]"""

import argparse
import sys

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .. import DEFAULT_DB
from ..link import find_port
from .window import MainWindow


def dark_palette():
    p = QPalette()
    base, window, text = QColor("#161b22"), QColor("#0d1117"), QColor("#e6edf3")
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor("#1c2230"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#21262d"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Highlight, QColor("#1f6feb"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.PlaceholderText, QColor("#7d8590"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#6e7681"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6e7681"))
    return p


def main():
    ap = argparse.ArgumentParser(description="ESP32 WiFi + Bluetooth scanner GUI")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--mode", choices=["wifi", "bt", "both"], default="both")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite capture log")
    ap.add_argument("--no-record", action="store_true", help="don't log scans to the db")
    args = ap.parse_args()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ESP32 Scanner")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    pg.setConfigOptions(antialias=True, foreground="#8b949e")

    win = MainWindow(port=args.port or find_port(), mode=args.mode,
                     db_path=args.db, record=not args.no_record)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
