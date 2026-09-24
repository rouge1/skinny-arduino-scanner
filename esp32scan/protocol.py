"""The firmware's serial protocol.

The host sends single-character commands; in JSON mode ('j') the firmware
answers with one JSON object per line, each tagged with an "ev" field:

  hello       {"fw","ver","mode","mac"}           reply to 'j', 't', '?'
  mode        {"mode"}                            after a mode change
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
MODE_CMDS = {"wifi": b"w", "bt": b"b", "both": b"x"}
MODE_LABELS = {
    "wifi": "WiFi",
    "bt": "Bluetooth",
    "both": "WiFi + Bluetooth",
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
