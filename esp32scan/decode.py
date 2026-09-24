"""Turns the firmware's raw scan rows into display fields.

Link calls enrich() on every wifi/ble row in the reader thread, so the GUI,
the CLI and the store all see the same decoded fields.
"""

from . import addata, oui

# esp_ble_addr_type_t as reported by the ESP32 (bit 0 = random).
# The random subtype is in the two most significant bits of the address
# (Core v6.0 Vol 6 Part B §1.3.2).
RANDOM_KINDS = {0b11: "static", 0b01: "RPA", 0b00: "NRPA", 0b10: "random?"}
KIND_HELP = {
    "public": "Public address: fixed, registered with the IEEE (has an OUI vendor).",
    "static": "Random static: fixed until the device reboots or is reset.",
    "RPA": "Resolvable private address: changes about every 15 min; only bonded "
           "devices can tell it's the same device. Phones, watches, earbuds.",
    "NRPA": "Non-resolvable private address: a throwaway that can't be linked "
            "back to the device, even by bonded peers. Common for broadcast-only beacons.",
    "random?": "Random address of a reserved subtype.",
}


def address_kind(addr, at):
    if not at & 1:
        return "public"
    return RANDOM_KINDS[int(addr[:2], 16) >> 6]


def enrich(kind, row):
    if kind == "wifi":
        v = oui.vendor(row["bssid"])
        row["vendor"] = v or ("(virtual/local)" if oui.is_local(row["bssid"]) else "")
        return row

    adv = bytes.fromhex(row.get("adv", ""))
    structures, errors = addata.parse(adv)
    info = addata.summary(structures)
    row["kind"] = address_kind(row["addr"], row.get("at", 0))
    row["structures"] = structures
    row["ad_errors"] = errors
    row["name"] = info.get("name", "")
    row["company"] = info.get("company", "")
    row["oui"] = oui.vendor(row["addr"]) if row["kind"] == "public" else None
    row["info"] = info
    return row


def maker(row):
    """Best guess at who made a BLE device: the manufacturer data's company,
    else the OUI of a public address."""
    company = row.get("company") or ""
    if company.startswith("unknown company"):
        company = ""
    return company or row.get("oui") or ""


def ble_notes(info):
    """One-line summary of the interesting advertised extras."""
    # Apple message types without a known name ("type 0x16") stay in Details.
    apple = [a for a in dict.fromkeys(info.get("apple", [])) if not a.startswith("type 0x")]
    parts = [info.get("beacon"), info.get("uri"), ", ".join(apple) or None,
             info.get("appearance"),
             "encrypted data" if info.get("encrypted") else None]
    return "; ".join(p for p in parts if p)


def services_text(info):
    """Service names without their 0xNNNN prefix where a name is known."""
    out = []
    for s in info.get("services", []):
        out.append(s.split(" ", 1)[1] if s.startswith("0x") and " " in s else s)
    return ", ".join(out)


def ble_details(row):
    """Multi-line description of one BLE device's latest advertisement."""
    kind = row.get("kind", "")
    lines = [f"{row['addr']}   {kind}", f"  {KIND_HELP.get(kind, '')}"]
    if row.get("oui"):
        lines.append(f"  OUI vendor: {row['oui']}")
    who = maker(row)
    if who:
        lines.append(f"  Maker: {who}")
    lines.append("")
    structures = row.get("structures") or []
    if structures:
        lines.append("Advertising data (latest advertisement + scan response):")
        for a in structures:
            lines.append(f"  0x{a.type:02X} {a.name}: {a.text}")
    else:
        lines.append("No advertising data.")
    if row.get("ad_errors"):
        lines.append("AD errors: " + "; ".join(row["ad_errors"]))
    raw = bytes.fromhex(row.get("adv", ""))
    lines += ["", f"Raw ({len(raw)} bytes): {raw.hex(' ').upper()}"]
    return "\n".join(lines)
