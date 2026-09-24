# CLAUDE.md

ESP32 WiFi + BLE scanner: Arduino firmware on an ESP32 DevKit streams scan
results as JSON lines over USB serial to a PySide6 desktop GUI on this Linux
PC. See README.md for features, protocol and DB schema.

## Commands

```bash
arduino-cli compile tools/esp32-ai          # FQBN + partition come from sketch.yaml
arduino-cli upload  tools/esp32-ai          # port /dev/ttyUSB0 from sketch.yaml
./scanner-gui                               # GUI (.venv/bin/python -m esp32scan.gui)
ESP32_MODE=x ./serial-monitor.py 20         # headless capture for 20 s
.venv/bin/pip install -r requirements.txt   # venv made with ~/miniconda3/bin/python
```

No test suite. To verify GUI changes without a display, run it with
`QT_QPA_PLATFORM=offscreen`, drive it with `QTimer.singleShot`, save
`window.grab()` to a PNG and look at it. Use a scratch `db_path` so you don't
pollute `data/captures.db`.

## Architecture

- `tools/esp32-ai/esp32-ai.ino` holds the firmware. The main loop alternates
  WiFi/BLE scans and prints either text tables (default after boot) or JSON
  lines (after `j`).
- `esp32scan/protocol.py` documents the wire protocol. **Keep it in sync with
  the firmware.** Any field added in the `.ino` must be handled in
  `link.py` / `store.py` / `gui/models.py`.
- `esp32scan/link.py` `Link.poll()` buffers `wifi`/`ble` rows and emits one
  `("scan", kind, index, rows, done)` item per `scan_done`. It also
  re-handshakes (`j` + mode + `?`) on the boot banner or 4 s without JSON.
- `esp32scan/store.py` holds the SQLite log shared by GUI and CLI. Schema
  changes go through the `PRAGMA table_info` migration in `Store.__init__`.
  Don't break the existing tables.
- `esp32scan/gui/`: `worker.py` (QThread that owns the serial port *and* the
  DB connection, so all serial writes go through its command queue),
  `models.py` (append-only `DeviceModel` + `DeviceFilter` proxy; rows are
  never reordered, so selection survives updates), `widgets.py` (RSSI bar
  delegate, `ChannelMap`, `SignalHistory`), `window.py`.

## Gotchas

- **Partition scheme**: WiFi + BLE is about 1.6 MB, too big for the default
  1.2 MB app partition. `sketch.yaml` pins `PartitionScheme=huge_app`. Don't
  drop it.
- **BLE heap**: `setAdvertisedDeviceCallbacks(cb, true)` (wantDuplicates) is
  deliberate. With `false`, the BLE library keeps its own copy of every
  device, and in a crowded area (100+ devices here) that exhausts the heap →
  `bad_alloc` abort in `ScanCB::onResult`. `g_bt` is also pre-reserved and
  capped at `MAX_BLE_DEVICES`. Free heap is reported in `scan_done.heap` and
  should stay around 48 KB.
- **Opening the port resets the ESP32** (CP2102 DTR/RTS). It comes back in
  text mode; `Link` handles that. Only one process can hold the port.
- Decode firmware crashes with
  `~/.arduino15/packages/esp32/tools/esp-x32/*/bin/xtensa-esp32-elf-addr2line -pfiaC -e <build>/esp32-ai.ino.elf <addrs>`
  (build with `arduino-cli compile --output-dir <dir>` to get the ELF).
- `arduino-cli monitor` prints nothing when run non-interactively. Read the
  serial port with pyserial (`dtr=False, rts=False`).
- PySide6 6.11 needs scoped enums (`QStyle.ControlElement.CE_ItemViewItem`,
  `QPainter.RenderHint.Antialiasing`). Short forms via an instance fail.
- `arduino-cli board list` shows the CP2102 board as "Unknown"; that's normal.
- The user is in `dialout` and the desktop session has it, so no `sg dialout`
  wrapper is needed.
