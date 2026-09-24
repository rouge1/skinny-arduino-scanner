"""Background thread that owns the serial link and the SQLite store."""

import queue

import serial
from PySide6.QtCore import QThread, Signal

from ..link import Link
from ..store import Store


class SerialWorker(QThread):
    raw = Signal(str)
    event = Signal(dict)
    scan = Signal(str, int, list, dict)  # kind, scan index, rows, scan_done event
    status = Signal(str, bool)           # message, connected
    session = Signal(str)                # db session name ("" when not recording)

    def __init__(self, port, mode, db_path, record):
        super().__init__()
        self.port = port
        self.mode = mode
        self.db_path = db_path
        self.record = record
        self._cmds = queue.Queue()
        self._stop = False

    def set_mode(self, mode):
        self._cmds.put(("mode", mode))

    def send_raw(self, data):
        self._cmds.put(("raw", data))

    def stop(self):
        self._stop = True

    def run(self):
        link = Link(self.port, self.mode)
        try:
            link.open()
        except (serial.SerialException, OSError) as e:
            hint = ""
            if isinstance(e, PermissionError) or "Permission denied" in str(e):
                hint = " (is your user in the dialout group? check with: id -nG)"
            self.status.emit(f"Cannot open {self.port}: {e.strerror or e}{hint}", False)
            return

        store = None
        if self.record:
            store = Store(self.db_path, self.mode, self.port)
            self.session.emit(store.name)
        self.status.emit(f"Connected to {self.port}", True)

        try:
            while not self._stop:
                while not self._cmds.empty():
                    what, arg = self._cmds.get()
                    if what == "mode":
                        link.set_mode(arg)
                        if store and arg != "idle":
                            store.set_mode(arg)
                    else:
                        link.send(arg)
                for item in link.poll():
                    if item[0] == "raw":
                        self.raw.emit(item[1])
                    elif item[0] == "event":
                        self.event.emit(item[1])
                    else:
                        _, kind, index, rows, done = item
                        if store:
                            store.save_scan(kind, index, rows)
                        self.scan.emit(kind, index, rows, done)
        except (serial.SerialException, OSError) as e:
            self.status.emit(f"Lost connection: {e}", False)
            return
        finally:
            link.close()
            if store:
                store.close()
        self.status.emit("Disconnected", False)
