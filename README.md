# ESP32 WiFi + Bluetooth Scanner

An ESP32 DevKit scans nearby **WiFi networks** and **Bluetooth LE devices**
and streams the results over USB to a desktop GUI on this PC. The GUI shows
them live, charts signal strength over time and logs every scan to SQLite.

```
┌──────────────┐  USB serial (460800)   ┌────────────────────────────┐
│ ESP32 DevKit │ ─── JSON lines ──────▶ │ scanner-gui (PySide6)      │
│ esp32-ai.ino │ ◀── 1-char commands ── │  tables · channel map ·    │
└──────────────┘                        │  signal history · CSV      │
                                        └─────────────┬──────────────┘
                                                      ▼
                                              data/captures.db
```

## Features

- **WiFi tab**: SSID, BSSID, vendor (from the IEEE OUI registry), channel,
  security, live signal bar, best signal seen, how many scans it appeared in,
  first/last seen. Hidden networks are included. BSSIDs with the
  locally-administered bit set show as `(virtual/local)`: they are the extra
  virtual networks an access point runs next to its main one, so they have no
  vendor.
- **Channel map**: the usual WiFi-analyser view, where each network is an arch
  over its 2.4 GHz channel. It shows at a glance how crowded channels 1/6/11
  are.
- **Bluetooth tab**: address and its **type** (public / random static / RPA /
  NRPA; hover for what each means), name, maker, signal, advertised TX power,
  services, and an Info column with beacons (iBeacon, Eddystone), Apple
  Continuity messages (Find My, Nearby Info, AirPods pairing, AirPlay…) and
  appearance. The ESP32 sends each device's raw advertising payload, and the
  PC decodes it against the Bluetooth SIG's assigned numbers (4,000+
  companies, service UUIDs, appearance values). The maker comes from the
  manufacturer data, or from the OUI for public addresses.
- **Details** (Bluetooth): every advertising data structure of the selected
  device, decoded, plus the raw bytes.
- **Signal history**: RSSI over the last 10 minutes for the selected rows
  (or the strongest few when nothing is selected). Multi-select works.
- Devices persist across scans. Rows missing from the latest scan turn grey.
  "Only in latest scan" hides them.
- Filter box matches any column. Every column is sortable.
- Switch between **WiFi / Bluetooth / Both** and **Pause** (Space) while
  connected.
- **Survey** tab: walk a route through the building and build WiFi and
  Bluetooth **heat maps** on the floor plan (see below).
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
tools/update-oui.py           re-download the IEEE OUI registries
serial-monitor.py             symlink → tools/serial-monitor.py
esp32scan/                    Python package shared by GUI and CLI
  protocol.py                   serial protocol constants, JSON parsing
  link.py                       port discovery, serial link, scan grouping
  decode.py                     raw rows → display fields (address type, maker, notes)
  addata.py, assigned/          BLE advertising-data decoder + SIG assigned numbers
                                (vendored from ble-scanner)
  oui.py, ieee/oui.tsv.gz       MAC vendor lookup (IEEE MA-L/MA-M/MA-S)
  store.py                      SQLite capture log
  survey.py                     survey storage, per-stop values, IDW heat map
  gui/                          PySide6 + pyqtgraph desktop app
scanner-gui                   GUI launcher
routes/                       survey routes (floor plan + stop positions)
knowledge/                    board specs, photo, floor plan
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

## Heat-map survey

Carry the laptop with the ESP32 plugged in, stop at each spot on a route, and
use the board's **BOOT** button:

| BOOT | Effect | LED |
|---|---|---|
| short press | start capturing at this stop, or stop the capture | solid while capturing |
| hold ≥ 1 s | skip this stop (cancels a capture in progress) | 3 quick flashes |

**EN is reset. Don't use it during a survey.**

1. Start `./scanner-gui` and open the **Survey** tab. It loads the first route
   in `routes/`. The panel shows the next stop.
2. At each stop, press BOOT, wait **20–30 s**, then press BOOT again. Only
   scans that ran entirely inside the capture count, and a WiFi + Bluetooth
   cycle takes about 8.5 s, so 30 s gives about 3 of each. A capture with no
   complete scan is rejected.
3. The stop turns green and the map updates. Clicking a stop (on the map or
   in the list) makes it the next one to capture, which is how you redo one.
   **Start / stop capture** and **Skip stop** do the same as the button.

The first capture starts a new survey. Earlier surveys can be picked from the
drop-down to view or continue.

**Heat map layers**: coverage of one SSID (strongest AP broadcasting it), the
strongest signal of any network, one access point (BSSID), one Bluetooth
device, and the number of APs / Bluetooth devices heard. At each stop a
signal value is the strongest matching reading per scan, averaged over the
scans that heard it, or −100 dBm if none did. Between stops the value is
inverse-distance weighted, and the map fades out beyond **Reach** pixels from
the nearest stop instead of guessing. Bluetooth devices with RPA addresses
change address about every 15 minutes, so per-device maps work best for
public/static addresses.

**Routes** are JSON files in `routes/`: a floor plan image plus stops with
pixel positions on it.

```json
{"name": "UAH floor 1", "floorplan": "knowledge/IMG_1698.jpg",
 "stops": [{"name": "Meeting 106", "x": 268, "y": 250}, ...]}
```

## Serial protocol

The host sends single characters:

| Cmd | Effect |
|-----|--------|
| `w` / `b` / `x` | scan WiFi / Bluetooth / both (5 s WiFi, then 5 s BLE) |
| `s` | stop scanning |
| `j` / `t` | JSON-lines output / human-readable tables (default after boot) |
| `?` | print a status (`hello`) line |
| `m` / `k` | same as a short / long BOOT press |

The port runs at **460800 baud** (for a plain serial monitor:
`arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=460800`). The ESP32's boot
ROM always prints at 115200, so its few boot lines look like garbage at that
rate. That's expected.

In JSON mode, every line is one object with an `ev` field:

```json
{"ev":"hello","fw":"esp32-ai","ver":2,"mode":"both","mac":"04:B2:47:06:0A:C0"}
{"ev":"scan_start","kind":"wifi","scan":1}
{"ev":"wifi","scan":1,"bssid":"8C:30:66:DE:51:20","rssi":-54,"ch":6,"sec":"WPA2/3","ssid":"SkinnyRD"}
{"ev":"ble","scan":1,"addr":"90:70:69:10:be:e1","at":0,"rssi":-67,"adv":"02010611079eca…0f094c6f526120466f7848756e746572"}
{"ev":"scan_done","kind":"ble","scan":1,"count":134,"ms":5007,"t0":4301,"t1":9308,"heap":49040}
{"ev":"button","action":"start","mark":1,"t":4298}
```

`t`, `t0` and `t1` are the board's `millis()`. The app matches scans to
captures in board time, so serial delay doesn't matter. It also estimates
the PC−board clock offset (the smallest `PC receive time − board time` seen)
to put wall-clock times on captures.

`at` is the ESP32's address type (bit 0 set = random). `adv` is the device's
longest advertising payload in that scan, as hex. Advertisements that came
with a scan response include it (up to 62 bytes). The PC does all the
decoding.

Opening the port resets the board, which then boots in text mode. The host
watches for the boot banner and re-sends `j` + the mode. It also re-sends
them if no JSON arrives for 12 s, so a board reboot recovers by itself.

## Database

`data/captures.db` (SQLite, WAL) has three tables:

- `sessions`: one row per connection (name, mode, port, start/end time)
- `scans`: one row per completed scan (kind `wifi`/`ble`, index, time, count)
- `surveys`, `survey_points`, `survey_readings`: heat-map surveys, one row
  per survey, per captured or skipped stop (redoing a stop replaces it) and
  per network/device per scan at a stop. They're kept separately from the
  capture log, so surveys work with "Log to database" off.
- `entries`: one row per network/device per scan: `rssi`, `identifier`
  (BSSID or BLE address), `name` (SSID or BLE name), `channel`, `security`,
  and `extra` (JSON). For WiFi, `extra` holds the OUI `vendor`. For BLE it
  holds the address type (`at`, `kind`), the decoded `company` and the raw
  payload `adv`, which `esp32scan.addata.parse()` can decode again later.

Sessions recorded with older firmware differ. The very first one (from the
Mac mini) has the SSID in `identifier` for WiFi, and firmware v2 sessions
have BLE `mfr`/`tx`/`uuid` in `extra` instead of `adv`.

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
- **Repeated "(boot ROM output …)" lines in the Console**: the board is
  rebooting in a loop, usually because USB power sags during WiFi transmit
  bursts. Try another USB port or cable. An `abort()` / `Backtrace:` line is a
  firmware crash instead.
- **Vendors look out of date**: run `.venv/bin/python tools/update-oui.py`.
- **No data**: press **EN** on the board. If a flash was interrupted, hold
  **BOOT** and tap **EN**, then re-upload.
