# ESP32 WiFi + Bluetooth Scanner

An ESP32 DevKit scans nearby **WiFi networks** and **Bluetooth LE devices**
and streams the results over USB to a desktop GUI on this PC. The GUI shows
them live, charts signal strength over time and logs every scan to SQLite.

```
┌──────────────┐  USB serial (115200)   ┌────────────────────────────┐
│ ESP32 DevKit │ ─── JSON lines ──────▶ │ scanner-gui (PySide6)      │
│ esp32-ai.ino │ ◀── 1-char commands ── │  tables · channel map ·    │
└──────────────┘                        │  signal history · CSV      │
                                        └─────────────┬──────────────┘
                                                      ▼
                                              data/captures.db
```

## Features

- **WiFi tab**: SSID, BSSID, channel, security, live signal bar, best signal
  seen, how many scans it appeared in, first/last seen. Hidden networks are
  included.
- **Channel map**: the usual WiFi-analyser view, where each network is an arch
  over its 2.4 GHz channel. It shows at a glance how crowded channels 1/6/11
  are.
- **Bluetooth tab**: address, name, vendor (decoded from the manufacturer
  ID, e.g. Apple, Microsoft, Samsung), signal, advertised TX power, service
  UUID.
- **Signal history**: RSSI over the last 10 minutes for the selected rows
  (or the strongest few when nothing is selected). Multi-select works.
- Devices persist across scans. Rows missing from the latest scan turn grey.
  "Only in latest scan" hides them.
- Filter box matches any column. Every column is sortable.
- Switch between **WiFi / Bluetooth / Both** and **Pause** (Space) while
  connected.
- **Console** tab: raw serial output from the board, plus a box for sending
  commands by hand.
- **Log to database**: every scan is saved to `data/captures.db`.
  **File → Export … as CSV** saves the current table.

## Hardware

ESP32 DevKit V1 (ESP-WROOM-32, ESP32-D0WD-V3, 4 MB flash, CP2102 USB-UART).
On this PC it enumerates as `/dev/ttyUSB0`. Full specs are in
[knowledge/SPECS.md](knowledge/SPECS.md).

## Layout

```
tools/esp32-ai/esp32-ai.ino   firmware (Arduino / ESP32 core 3.x)
tools/esp32-ai/sketch.yaml    default FQBN (huge_app partition) + port
tools/serial-monitor.py       headless terminal monitor + DB logger
serial-monitor.py             symlink → tools/serial-monitor.py
esp32scan/                    Python package shared by GUI and CLI
  protocol.py                   serial protocol constants, JSON parsing, BLE vendor table
  link.py                       port discovery, serial link, scan grouping
  store.py                      SQLite capture log
  gui/                          PySide6 + pyqtgraph desktop app
scanner-gui                   GUI launcher
knowledge/                    board specs + photo
data/captures.db              capture log (created automatically, git-ignored)
```

## Setup

### Firmware

Requires `arduino-cli` with the `esp32:esp32` core (3.3.x).

```bash
arduino-cli compile tools/esp32-ai
arduino-cli upload  tools/esp32-ai
```

`sketch.yaml` supplies the board (`esp32:esp32:esp32`) with the
**Huge APP (3 MB, no OTA)** partition scheme. WiFi + BLE together are about
1.6 MB, which doesn't fit the default 1.2 MB app partition. It also sets
the port to `/dev/ttyUSB0`.

### Python

```bash
~/miniconda3/bin/python -m venv .venv      # system python3 lacks venv
.venv/bin/pip install -r requirements.txt
```

Your user needs to be in the `dialout` group to open the serial port
(`id -nG` should list it). If you were only just added, log out and back in.

## Usage

```bash
./scanner-gui                       # auto-detects the port and connects
./scanner-gui --mode wifi           # start in WiFi-only mode
./scanner-gui --port /dev/ttyUSB1 --no-record
```

Headless / terminal:

```bash
./serial-monitor.py                 # asks for mode, streams until Ctrl-C
ESP32_MODE=x ./serial-monitor.py 60 # both modes, stop after 60 s
```

Only one program can hold the serial port at a time. Close the GUI before
running the CLI, and vice versa.

## Serial protocol

The host sends single characters:

| Cmd | Effect |
|-----|--------|
| `w` / `b` / `x` | scan WiFi / Bluetooth / both (5 s WiFi, then 5 s BLE) |
| `s` | stop scanning |
| `j` / `t` | JSON-lines output / human-readable tables (default after boot) |
| `?` | print a status (`hello`) line |

In JSON mode, every line is one object with an `ev` field:

```json
{"ev":"hello","fw":"esp32-ai","ver":2,"mode":"both","mac":"04:B2:47:06:0A:C0"}
{"ev":"scan_start","kind":"wifi","scan":1}
{"ev":"wifi","scan":1,"bssid":"8C:30:66:DE:51:20","rssi":-54,"ch":6,"sec":"WPA2/3","ssid":"SkinnyRD"}
{"ev":"ble","scan":1,"addr":"59:c4:b6:26:d9:97","rssi":-58,"name":"","mfr":76,"tx":12}
{"ev":"scan_done","kind":"ble","scan":1,"count":147,"ms":5008,"heap":48472}
```

Opening the port resets the board, which then boots in text mode. The host
watches for the boot banner and re-sends `j` + the mode. It also re-sends
them if no JSON arrives for 4 s, so a board reboot recovers by itself.

## Database

`data/captures.db` (SQLite, WAL) has three tables:

- `sessions`: one row per connection (name, mode, port, start/end time)
- `scans`: one row per completed scan (kind `wifi`/`ble`, index, time, count)
- `entries`: one row per network/device per scan: `rssi`, `identifier`
  (BSSID or BLE address), `name` (SSID or BLE name), `channel`, `security`,
  and `extra` (JSON: BLE `mfr`, `tx`, `uuid`)

The first session in the file was recorded on the Mac mini with an older
firmware. Its WiFi rows have the SSID in `identifier` and no BSSID.

Example: strongest sighting of every network:

```sql
SELECT name AS ssid, identifier AS bssid, channel, MAX(rssi)
FROM entries WHERE kind = 'wifi' GROUP BY identifier ORDER BY 4 DESC;
```

## Troubleshooting

- **`Permission denied` on the port**: not in `dialout` (see Setup).
- **`text section exceeds available space`**: you compiled without
  `sketch.yaml`'s partition scheme. Pass
  `--fqbn esp32:esp32:esp32:PartitionScheme=huge_app`.
- **Repeated `rst:` / `ets Jul 29 2019` boot lines**: the board is rebooting
  in a loop, usually because USB power sags during WiFi transmit bursts. Try
  another USB port or cable. A `abort()` / `Backtrace:` line is a firmware
  crash instead; see the Console tab.
- **No data**: press **EN** on the board. If a flash was interrupted, hold
  **BOOT** and tap **EN**, then re-upload.
