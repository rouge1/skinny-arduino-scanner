#!/usr/bin/env python3
"""Terminal monitor + timestamped SQLite capture for the ESP32 scanner.

Headless counterpart to the GUI (./scanner-gui): puts the firmware in JSON
mode, prints each completed scan as a table, and records every row into the
same SQLite database. Each run gets a unique session name.

Modes:
  b = Bluetooth only
  w = WiFi only
  x = both (5 s WiFi, then 5 s Bluetooth, repeating)

Usage: ./serial-monitor.py [seconds]
  seconds: optional; omit to stream until Ctrl-C.

Env:
  ESP32_PORT  serial port (default: first /dev/ttyUSB* / /dev/ttyACM*)
  ESP32_MODE  b/w/x, skips the mode prompt
  ESP32_DB    path to sqlite db (default: <project>/data/captures.db)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serial  # noqa: E402

from esp32scan import DEFAULT_DB  # noqa: E402
from esp32scan.link import Link, find_port  # noqa: E402
from esp32scan.protocol import MODE_LABELS, vendor_name  # noqa: E402
from esp32scan.store import Store  # noqa: E402

KEYS = {"b": "bt", "w": "wifi", "x": "both"}


def log(msg):
    print(f"[serial-monitor] {msg}", file=sys.stderr, flush=True)


def ask_mode():
    env = os.environ.get("ESP32_MODE", "").strip().lower()
    if env in KEYS:
        return KEYS[env]
    if not sys.stdin.isatty():
        return "both"
    print("Scan mode?")
    print("  [b] Bluetooth")
    print("  [w] WiFi")
    print("  [x] Both (5s WiFi, then 5s Bluetooth)")
    while True:
        ans = input("Choose [b/w/x] (default x): ").strip().lower()
        if ans in ("", "x", "both"):
            return "both"
        if ans in ("b", "bluetooth"):
            return "bt"
        if ans in ("w", "wifi"):
            return "wifi"
        print("Please enter b, w, or x.")


def print_scan(kind, index, rows, done):
    secs = done.get("ms", 0) / 1000
    if kind == "wifi":
        print(f"\n=== WiFi scan #{index}  ({len(rows)} networks, {secs:.1f}s) ===")
        print("RSSI  CH  SEC       BSSID              SSID")
        for r in rows:
            print(f"{r['rssi']:4d}  {r['ch']:2d}  {r['sec']:<8}  {r['bssid']}  "
                  f"{r['ssid'] or '(hidden)'}")
    else:
        print(f"\n=== BLE scan #{index}  ({len(rows)} devices, {secs:.1f}s) ===")
        print("RSSI  ADDRESS            VENDOR        NAME")
        for r in rows:
            print(f"{r['rssi']:4d}  {r['addr']}  {vendor_name(r.get('mfr')):<12}  "
                  f"{r['name'] or '(unnamed)'}")
    sys.stdout.flush()


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else None
    mode = ask_mode()
    port = os.environ.get("ESP32_PORT") or find_port()
    db_path = os.environ.get("ESP32_DB", str(DEFAULT_DB))
    if not port or not os.path.exists(port):
        log(f"ERROR: no serial port found ({port}). Plug in the board or set ESP32_PORT.")
        sys.exit(1)
    log(f"mode={MODE_LABELS[mode]} port={port}")
    log(f"db={db_path}")

    link = Link(port, mode)
    try:
        link.open()
    except (serial.SerialException, OSError) as e:
        log(f"ERROR: cannot open {port}: {e}")
        log("Hint: close the GUI / any other serial monitor; check you're in the dialout group.")
        sys.exit(1)

    store = Store(db_path, mode, port)
    log(f"session={store.name}")
    log("streaming (Ctrl-C to stop)")

    end = time.time() + duration if duration else None
    scans = 0
    try:
        while end is None or time.time() < end:
            for item in link.poll():
                if item[0] == "raw":
                    print(item[1], flush=True)
                elif item[0] == "scan":
                    _, kind, index, rows, done = item
                    store.save_scan(kind, index, rows)
                    print_scan(kind, index, rows, done)
                    scans += 1
    except KeyboardInterrupt:
        pass
    finally:
        link.close()
        store.close()
        log(f"saved {scans} scans to session '{store.name}'")


if __name__ == "__main__":
    main()
