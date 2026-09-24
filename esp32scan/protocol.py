"""The firmware's serial protocol.

The host sends single-character commands; in JSON mode ('j') the firmware
answers with one JSON object per line, each tagged with an "ev" field:

  hello       {"fw","ver","mode","mac"}           reply to 'j', 't', '?'
  mode        {"mode"}                            after a mode change
  scan_start  {"kind","scan"}                     kind is "wifi" or "ble"
  wifi        {"scan","bssid","ssid","rssi","ch","sec"}
  ble         {"scan","addr","name","rssi", optional "mfr","tx","uuid"}
  scan_done   {"kind","scan","count","ms","heap"}  after that scan's rows

Anything that isn't a JSON object (boot ROM chatter, panics, the text banner)
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

# Bluetooth SIG company identifiers for common vendors (first two bytes of
# the manufacturer-specific advertising data).
BLE_VENDORS = {
    0x0002: "Intel",
    0x0006: "Microsoft",
    0x000A: "Qualcomm",
    0x000D: "Texas Instruments",
    0x000F: "Broadcom",
    0x001D: "Qualcomm",
    0x0046: "MediaTek",
    0x004C: "Apple",
    0x0059: "Nordic Semi",
    0x0075: "Samsung",
    0x0078: "Nike",
    0x0087: "Garmin",
    0x00C4: "LG",
    0x00E0: "Google",
    0x012D: "Sony",
    0x0131: "Cypress",
    0x0157: "Huami",
    0x0171: "Amazon",
    0x02E5: "Espressif",
    0x038F: "Xiaomi",
    0x0499: "Ruuvi",
    0x05A7: "Sonos",
}


def vendor_name(mfr):
    if mfr is None:
        return ""
    return BLE_VENDORS.get(mfr, f"0x{mfr:04X}")


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
