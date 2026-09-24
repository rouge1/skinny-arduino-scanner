"""Serial connection to the board: port discovery and a line-oriented reader
that keeps the firmware in JSON mode and groups rows into completed scans."""

import glob
import os
import time

import serial

from .decode import enrich
from .protocol import (BOOT_BANNER, CMD_JSON, CMD_STATUS, CMD_STOP, MODE_CMDS,
                       parse_line)
from .store import now_iso

BAUD = 460800
# Re-handshake after this long without JSON. Must exceed the longest silent
# stretch in normal operation: a 5 s BLE scan plus printing it.
SILENCE_S = 12
PORT_PATTERNS = ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*")


def list_ports():
    """Candidate serial ports, stable by-id names first."""
    seen, out = set(), []
    for pat in PORT_PATTERNS:
        for p in sorted(glob.glob(pat)):
            real = os.path.realpath(p)
            if real not in seen:
                seen.add(real)
                out.append(p)
    return out


def find_port():
    ports = list_ports()
    return ports[0] if ports else None


class Link:
    """Owns the serial port. Call poll() in a loop; it returns a list of
    ("raw", str) / ("event", dict) / ("scan", kind, index, rows, done_event)
    tuples."""

    def __init__(self, port, mode="both"):
        self.port = port
        self.mode = mode
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = BAUD
        self.ser.timeout = 0.1
        # Opening a CP210x port pulses DTR/RTS and resets the ESP32 anyway;
        # keeping them low stops the board being held in reset afterwards.
        self.ser.dtr = False
        self.ser.rts = False
        self._buf = b""
        self._rows = {"wifi": [], "ble": []}
        self._last_json = 0.0
        self._last_hello_req = 0.0

    def open(self):
        self.ser.open()
        self._last_json = time.time()
        self.handshake()

    def close(self):
        if self.ser.is_open:
            try:
                self.ser.write(CMD_STOP)
                self.ser.flush()
            except serial.SerialException:
                pass
            self.ser.close()

    def send(self, data):
        self.ser.write(data)
        self.ser.flush()

    def handshake(self):
        """Switch the firmware to JSON output and re-apply the scan mode."""
        cmd = MODE_CMDS.get(self.mode, CMD_STOP)
        self.send(CMD_JSON + cmd + CMD_STATUS)
        self._last_hello_req = time.time()

    def set_mode(self, mode):
        self.mode = mode
        self.send(MODE_CMDS.get(mode, CMD_STOP))

    def poll(self):
        out = []
        data = self.ser.read(self.ser.in_waiting or 1)
        if data:
            self._buf += data
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").rstrip("\r")
                out.extend(self._handle(line))
        # No JSON for a while (e.g. a reboot we missed): ask again.
        now = time.time()
        if (now - self._last_json > SILENCE_S and now - self._last_hello_req > SILENCE_S
                and self.mode != "idle"):
            self.handshake()
        return out

    def _handle(self, line):
        ev = parse_line(line)
        if ev is None:
            if line.count("\ufffd") >= 3:
                # The ROM bootloader always prints at 115200 baud, which reads
                # as garbage at our 460800. (Firmware panics use our baud.)
                return [("raw", "(boot ROM output at 115200 baud, not shown)")]
            if BOOT_BANNER in line:
                # Board rebooted; setup() needs a moment before it reads serial.
                time.sleep(0.3)
                self.handshake()
            return [("raw", line)] if line.strip() else []

        self._last_json = time.time()
        kind = ev["ev"]
        if kind in ("wifi", "ble"):
            ev["_at"] = now_iso()
            self._rows[kind].append(enrich(kind, ev))
            return []
        if kind == "scan_start":
            self._rows[ev["kind"]] = []
            return [("event", ev)]
        if kind == "scan_done":
            rows, self._rows[ev["kind"]] = self._rows[ev["kind"]], []
            return [("scan", ev["kind"], ev["scan"], rows, ev)]
        return [("event", ev)]
