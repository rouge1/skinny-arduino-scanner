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

The window has a rail on the left: the pages (WiFi, Bluetooth, Survey,
Console, with live counts; Ctrl+1–4) and, below them, the board's controls
(port, connect, what to scan, pause, free heap, database logging).

- **Signal meter**: every signal column is a segmented bar, one segment per
  5 dB, coloured with the same weak→strong scale as the heat map, so a colour
  means the same dBm everywhere. A dimmer segment further right marks the
  best reading so far (peak hold).
- **Columns**: each table shows the useful few by default. Right-click a
  column header to show the rest (best, seen count, first seen, TX power,
  services…). The choice is remembered.
- **WiFi**: SSID, BSSID, vendor (from the IEEE OUI registry), channel,
  security, signal with best, how many scans it appeared in,
  first/last seen. Hidden networks are included. BSSIDs with the
  locally-administered bit set show as `(virtual/local)`: they are the extra
  virtual networks an access point runs next to its main one, so they have no
  vendor.
- **Channel map**: the usual WiFi-analyser view, where each network is an arch
  over its 2.4 GHz channel. It shows at a glance how crowded channels 1/6/11
  are.
- **Bluetooth**: address and its **type** (public / random static / RPA /
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
- Devices persist across scans. Rows missing from the latest scan fade.
  "Latest scan only" hides them.
- The search box (Ctrl+F) matches any column. Every column is sortable.
- Switch between **WiFi / Bluetooth / Both / Fast** and **Pause scanning**
  (Space) while connected. **Fast** is Bluetooth only with 1 s scans, so it
  updates about every 1.3 s instead of every 5–8 s. Use it to track down one
  device: type its name or address in the search box, click its row, and
  watch the signal history as you walk (it catches slightly fewer devices per
  scan, about 97 vs 130 here, because slow advertisers can miss a 1 s window).
- **Survey**: walk a route through the building and build WiFi and
  Bluetooth **heat maps** on the floor plan (see below).
- **Console**: raw serial output from the board, plus a box for sending
  commands by hand.
- **Save scans to the database**: every scan is saved to `data/captures.db`.
  **Export CSV** above each table saves it; **Forget all** clears both lists.

## Hardware

ESP32 DevKit V1 (ESP-WROOM-32, ESP32-D0WD-V3, 4 MB flash, CP2102 USB-UART).
On this PC it enumerates as `/dev/ttyUSB0`. Full specs are in
[knowledge/SPECS.md](knowledge/SPECS.md).

## Layout

```
tools/esp32-ai/esp32-ai.ino   firmware (Arduino / ESP32 core 3.x)
tools/esp32-ai/sketch.yaml    default FQBN (no_ota partition: 2 MB app + 2 MB log) + port
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
**No OTA (2 MB app / 2 MB SPIFFS)** partition scheme. WiFi + BLE together
are about 1.7 MB, which doesn't fit the default 1.2 MB app partition, and the
2 MB data partition holds the survey log (LittleFS). It also sets
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

Walk a route, stopping at each spot, and use the board's **BOOT** button.
Either run the ESP32 **on a USB power bank** and import the captures
afterwards, or carry the laptop with the board plugged in and watch the map
fill in live.

| BOOT | Effect | LED |
|---|---|---|
| short press | start capturing at this stop | solid while capturing |
| short press again | stop: capture accepted | N slow blinks = stop N done |
| | or rejected as too short (no complete WiFi *and* BLE scan inside) | fast flicker for 2 s; capture again |
| hold ≥ 1 s | skip this stop (cancels a capture in progress) | 3 quick flashes, then N slow blinks |
| (idle) | scanning, not capturing | short blip every 2 s |
| (power-up) | | N slow blinks = stops already stored, 2 quick flashes = none |

**EN is reset. Don't use it during a survey.**

### On a power bank (no laptop)

Until an app connects, the board stores every capture in its flash (2 MB,
about 46 KB per stop, so 40+ stops fit). Stops are taken in route order:
each accepted capture or skip is the next stop, and rejected captures
don't count. Stored captures survive power loss; after a restart the LED
blinks how many stops are stored, and the walk continues where it left off.

1. Plug the board into the power bank. After a few seconds you get two
   quick flashes, then a blip every 2 s.
2. At each stop: press BOOT, wait 20–30 s, press BOOT. Count the blinks.
3. Back at the PC: plug the board in, run `./scanner-gui`, open **Survey**
   and click **Import from board**. The captures become a survey named
   "… (from board)". The app then offers to clear the board for the next walk.

Some power banks switch off when the current draw is low. The ESP32 draws
about 100–150 mA while scanning, which keeps most banks on. If yours cuts
out, the LED stops blipping.

### With the laptop

1. Start `./scanner-gui` and open **Survey**. (While an app is
   connected the board doesn't store captures in flash.) It loads the first route
   in `routes/`. The panel shows the next stop.
2. At each stop, press BOOT, wait **20–30 s**, then press BOOT again. Only
   scans that ran entirely inside the capture count, and a WiFi + Bluetooth
   cycle takes about 8.5 s, so 30 s gives about 3 of each. A capture with no
   complete scan is rejected.
3. The stop's badge fills in and the map updates. Selecting a stop in the list
   (or clicking one on the map that isn't captured yet) makes it the next one
   to capture, which is how you redo one. **Start capture** / **Stop
   capture** and **Skip stop** do the same as the button.

**Clicking the map** anywhere near a captured stop fills the **Devices at
<stop>** panel under the map with every WiFi network and Bluetooth device
heard there: average and best signal, how many of the stop's scans heard it,
maker, channel/security (WiFi), address type, services and decoded info
(Bluetooth). The **WiFi** / **Bluetooth** chips (with their counts) and the
search box narrow the list. The chips also switch the map to match: WiFi only
shows **access points heard**, Bluetooth only **devices heard**, and both
**everything heard**. Drag the divider to give the map or the list more room.

To work out **what is in a particular room**:
- The **signal slider** ("Any signal", or "≥ −70 dBm" etc.) hides anything
  weaker than the threshold at this stop.
  Roughly, −60 dBm or better is usually the same room and −80 or worse is far
  away, but transmit power varies a lot (a phone vs. a beacon vs. an AP).
- **Strongest here only** keeps only devices whose strongest reading over the
  whole walk was at this stop. The **Loudest at** column shows that stop for
  every device. This is the better test, because a device heard at −70 here
  but −55 next door is next door.
- Together (e.g. strongest here and ≥ −70 dBm) they give a short list of likely
  in-room devices. It's room-level at best: bodies, walls and orientation
  shift RSSI by 5–10 dB, and phones' RPA addresses rotate about every 15 min.

The number beside each stop on the map is the current layer's value there,
with its unit: a signal in **dBm**, or for the "heard" layers the **average
count per scan** (**APs** = WiFi access points, **BT** = Bluetooth devices,
**dev** = both together). Clicking a stop rings it on the map and highlights
the same stop in the list on the left. A count is lower than the total in the Devices panel, because not every device
is caught in every scan.

The first capture starts a new survey. Earlier surveys can be picked from the
drop-down to view or continue.

**Heat map layers**: coverage of one SSID (strongest AP broadcasting it), the
strongest signal of any network, one access point (BSSID), one Bluetooth
device, and the number of APs / Bluetooth devices / both heard. At each stop a
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
| `f` / `n` | fast (1 s) / normal (5 s) Bluetooth scans |
| `s` | stop scanning |
| `j` / `t` | JSON-lines output / human-readable tables (default after boot) |
| `?` | print a status (`hello`) line |
| `m` / `k` | same as a short / long BOOT press |
| `d` / `c` | dump / clear the stored survey log |

The port runs at **460800 baud** (for a plain serial monitor:
`arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=460800`). The ESP32's boot
ROM always prints at 115200, so its few boot lines look like garbage at that
rate. That's expected.

In JSON mode, every line is one object with an `ev` field:

```json
{"ev":"hello","fw":"esp32-ai","ver":6,"mode":"both","ble_s":5,"mac":"04:B2:47:06:0A:C0"}
{"ev":"scan_start","kind":"wifi","scan":1}
{"ev":"wifi","scan":1,"bssid":"8C:30:66:DE:51:20","rssi":-54,"ch":6,"sec":"WPA2/3","ssid":"SkinnyRD"}
{"ev":"ble","scan":1,"addr":"90:70:69:10:be:e1","at":0,"rssi":-67,"adv":"02010611079eca…0f094c6f526120466f7848756e746572"}
{"ev":"scan_done","kind":"ble","scan":1,"count":134,"ms":5007,"t0":4301,"t1":9308,"heap":49040}
{"ev":"button","action":"start","mark":1,"t":4298}
```

Button `action`s are `start`, `stop`, `reject` (too short), `skip` and `full`
(log full). `d` sends `{"ev":"log_begin","bytes":…,"stops":…}`, then each
stored line prefixed with `L`, then `{"ev":"log_end"}`. The stored lines are
the same JSON, plus a `{"ev":"boot"}` line after each power-up.

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
  capture log, so surveys work with "Save scans to the database" off.
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
  `--fqbn esp32:esp32:esp32:PartitionScheme=no_ota`.
- **Repeated "(boot ROM output …)" lines in the Console**: the board is
  rebooting in a loop, usually because USB power sags during WiFi transmit
  bursts. Try another USB port or cable. An `abort()` / `Backtrace:` line is a
  firmware crash instead.
- **Vendors look out of date**: run `.venv/bin/python tools/update-oui.py`.
- **No data**: press **EN** on the board. If a flash was interrupted, hold
  **BOOT** and tap **EN**, then re-upload.
