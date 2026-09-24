"""The firmware's serial protocol.

The host sends single-character commands; in JSON mode ('j') the firmware
answers with one JSON object per line, each tagged with an "ev" field:

  hello       {"fw","ver","mode","ble_s","mac"}   reply to 'j', 't', '?'
  mode        {"mode","ble_s"}                    after a mode or scan-length change
                                                  (ble_s: Bluetooth scan seconds, 5 or 1)
  scan_start  {"kind","scan"}                     kind is "wifi" or "ble"
  wifi        {"scan","bssid","ssid","rssi","ch","sec"}
  ble         {"scan","addr","at","rssi","adv"}      at: address type (bit 0 = random);
                                                  adv: raw advertising payload (AD
                                                  structures, incl. scan response), hex
  scan_done   {"kind","scan","count","ms","heap"}  after that scan's rows

The serial port runs at 460800 baud. Anything that isn't a JSON object (boot ROM chatter, panics, the text banner)
is passed through as raw text.
"""

import json

# Serial command bytes understood by the firmware.
CMD_JSON = b"j"
CMD_TEXT = b"t"
CMD_STATUS = b"?"
CMD_STOP = b"s"
# Each app mode sets the scan kinds and the Bluetooth scan length: "n" = normal
# 5 s scans, "f" = fast 1 s scans ("fast" is Bluetooth only, for hunting down
# one device).
MODE_CMDS = {"wifi": b"wn", "bt": b"bn", "both": b"xn", "fast": b"bf"}
MODE_LABELS = {
    "wifi": "WiFi",
    "bt": "Bluetooth",
    "both": "WiFi + Bluetooth",
    "fast": "Bluetooth, fast",
    "idle": "Stopped",
}

# Printed by setup(); seeing it means the board just (re)booted and has
# fallen back to text mode.
BOOT_BANNER = "ESP32 Collector"

def parse_line(line):
    """Return the event dict for a JSON line, or None for anything else."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    if not isinstance(ev, dict) or "ev" not in ev:
        return None
    return ev
