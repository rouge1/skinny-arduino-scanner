"""Advertising data: AD structures (GAP §11) and the meaning of each AD type (Supplement Part A).

Vendored from ble-scanner (github.com/rouge1/skinny-ble-scanner,
tools/blescan/addata.py at efcc2f0) together with assigned/. Changes made for this project
are marked "esp32-ai". The spec references are to Core v6.0 Vol 3 Part C §11 and the Core
Specification Supplement v12 Part A; ble-scanner's knowledge/ folder has them converted.
Names come from the SIG's assigned numbers in assigned/ (see its README).

parse() returns the AD structures, each with a readable rendering, and summary() the fields
a device table wants (name, company, services, TX power...).
"""
import os
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

import yaml


HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assigned")
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_Loader)


@lru_cache(maxsize=None)
def ad_type_names():
    return {e["value"]: e["name"] for e in _load("ad_types.yaml")["ad_types"]}


@lru_cache(maxsize=None)
def company_names():
    return {e["value"]: e["name"] for e in _load("company_identifiers.yaml")["company_identifiers"]}


@lru_cache(maxsize=None)
def uuid16_names():
    names = {e["uuid"]: e["name"] for e in _load("member_uuids.yaml")["uuids"]}
    names.update({e["uuid"]: e["name"] for e in _load("service_uuids.yaml")["uuids"]})
    return names


@lru_cache(maxsize=None)
def appearance_names():
    """16-bit Appearance value -> (category, subcategory or None): category in bits 15-6."""
    out = {}
    for c in _load("appearance_values.yaml")["appearance_values"]:
        out[c["category"] << 6] = (c["name"], None)
        for sub in c.get("subcategory", []):
            out[(c["category"] << 6) | sub["value"]] = (c["name"], sub["name"])
    return out


@lru_cache(maxsize=None)
def uri_schemes():
    return {e["value"]: e["name"] for e in _load("uri_schemes.yaml")["uri_schemes"]}


def company_name(cid):
    return company_names().get(cid, f"unknown company 0x{cid:04X}")


def uuid16_text(u):
    name = uuid16_names().get(u)
    return f"0x{u:04X} {name}" if name else f"0x{u:04X}"


def appearance_text(value):
    names = appearance_names()
    if value in names:
        cat, sub = names[value]
        return f"{cat}: {sub}" if sub else cat
    if (value & ~0x3F) in names:
        return f"{names[value & ~0x3F][0]} (subcategory {value & 0x3F})"
    return f"unknown appearance 0x{value:04X}"


FLAG_BITS = ("LE Limited Discoverable", "LE General Discoverable", "BR/EDR not supported",
             "LE and BR/EDR (controller)", "bit 4 (previously used)")
LE_ROLES = {0: "peripheral only", 1: "central only", 2: "peripheral and central, peripheral preferred",
            3: "peripheral and central, central preferred"}
# Apple's Continuity messages inside its manufacturer data: [type][length][value]... These
# names are from public reverse engineering (furiousMAC/continuity; Martin et al., "Handoff
# All Your Privacy", PETS 2019), not from Apple.
APPLE_TYPES = {0x02: "iBeacon", 0x03: "AirPrint", 0x05: "AirDrop", 0x06: "HomeKit",
               0x07: "Proximity Pairing (AirPods, Beats)", 0x08: "Hey Siri", 0x09: "AirPlay Target",
               0x0A: "AirPlay Source", 0x0B: "Magic Switch", 0x0C: "Handoff",
               0x0D: "Tethering Target", 0x0E: "Tethering Source", 0x0F: "Nearby Action",
               0x10: "Nearby Info", 0x12: "Find My"}
APPLE = 0x004C
EDDYSTONE = 0xFEAA
EDDYSTONE_URL_SCHEMES = ("http://www.", "https://www.", "http://", "https://")
EDDYSTONE_URL_CODES = (".com/", ".org/", ".edu/", ".net/", ".info/", ".biz/", ".gov/",
                       ".com", ".org", ".edu", ".net", ".info", ".biz", ".gov")


@dataclass
class ADStructure:
    type: int
    data: bytes
    name: str
    text: str                  # readable rendering of the value
    fields: dict = field(default_factory=dict)


def _format_address(six):
    """esp32-ai: ble-scanner's linklayer.format_address. Six octets as sent (LSB first) ->
    'AA:BB:CC:DD:EE:FF', most significant first."""
    return ":".join(f"{b:02X}" for b in reversed(six))


def _u16(b, i=0):
    return b[i] | (b[i + 1] << 8)


def _uuid128(b):
    return str(uuid.UUID(bytes=bytes(reversed(b[:16]))))


def _hex(b):
    return b.hex(" ").upper() if b else "(empty)"


def _uuid_list(data, size):
    if size == 2:
        return [uuid16_text(_u16(data, i)) for i in range(0, len(data) - 1, 2)]
    if size == 4:
        return [f"0x{int.from_bytes(data[i:i + 4], 'little'):08X}" for i in range(0, len(data) - 3, 4)]
    return [_uuid128(data[i:i + 16]) for i in range(0, len(data) - 15, 16)]


def _decode(t, d):
    """(text, fields) for one AD structure's data."""
    if t == 0x01:
        bits = [n for i, n in enumerate(FLAG_BITS) if d and d[0] >> i & 1]
        return ", ".join(bits) or "none set", {"flags": d[0] if d else 0}
    if t in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x1F):
        size = {0x02: 2, 0x03: 2, 0x14: 2, 0x04: 4, 0x05: 4, 0x1F: 4}.get(t, 16)
        uuids = _uuid_list(d, size)
        key = "solicited" if t in (0x14, 0x15, 0x1F) else "services"
        return ", ".join(uuids) or "(none)", {key: uuids}
    if t in (0x08, 0x09):
        name = d.decode("utf-8", "replace").rstrip("\x00")
        return name, {"name": name, "name_complete": t == 0x09}
    if t == 0x0A and len(d) >= 1:
        p = int.from_bytes(d[:1], "little", signed=True)
        return f"{p} dBm", {"tx_power": p}
    if t == 0x12 and len(d) >= 4:
        lo, hi = _u16(d), _u16(d, 2)
        fmt = lambda v: "no preference" if v == 0xFFFF else f"{v * 1.25:g} ms"
        return f"{fmt(lo)} to {fmt(hi)}", {}
    if t in (0x16, 0x20, 0x21):
        size = {0x16: 2, 0x20: 4, 0x21: 16}[t]
        if len(d) < size:
            return _hex(d), {}
        u = _uuid_list(d[:size], size)[0]
        rest = d[size:]
        f = {"service_data": {u: rest.hex().upper()}, "services": [u]}
        if t == 0x16 and _u16(d) == EDDYSTONE:
            text, beacon = _eddystone(rest)
            f.update(beacon)
            return f"{u}: {text}", f
        return f"{u}: {_hex(rest)}", f
    if t == 0x19 and len(d) >= 2:
        a = _u16(d)
        return appearance_text(a), {"appearance": appearance_text(a)}
    if t in (0x17, 0x18):
        return ", ".join(_format_address(d[i:i + 6]) for i in range(0, len(d) - 5, 6)), {}
    if t in (0x1A, 0x2F) and len(d) >= 2:
        v = int.from_bytes(d, "little")
        return f"{v * 0.625:g} ms", {"adv_interval_ms": v * 0.625}
    if t == 0x1B and len(d) >= 7:
        return f"{_format_address(d[:6])} ({'random' if d[6] & 1 else 'public'})", {}
    if t == 0x1C and len(d) >= 1:
        return LE_ROLES.get(d[0], f"reserved 0x{d[0]:02X}"), {"le_role": LE_ROLES.get(d[0])}
    if t == 0x24:
        uri = _uri(d)
        return uri, {"uri": uri}
    if t == 0x31:
        return f"encrypted, {max(0, len(d) - 9)} octets of payload", {"encrypted": True}
    if t == 0xFF and len(d) >= 2:
        cid = _u16(d)
        text, f = f"{company_name(cid)}: {_hex(d[2:])}", {"company_id": cid, "company": company_name(cid),
                                                          "mfr_data": d[2:].hex().upper()}
        if cid == APPLE:
            apple, beacon = _apple(d[2:])
            if apple:
                text += f" ({', '.join(apple)})"
                f["apple"] = apple
            f.update(beacon)
        return text, f
    return _hex(d), {}


def _uri(d):
    s = d.decode("utf-8", "replace")
    if not s:
        return ""
    scheme = uri_schemes().get(ord(s[0]))
    if ord(s[0]) == 0x01 or scheme is None:
        return s[1:]
    return scheme + s[1:]


def _apple(d):
    """Continuity message names, and the iBeacon fields if there is one."""
    names, beacon, i = [], {}, 0
    while i + 2 <= len(d):
        t, n = d[i], d[i + 1]
        v = d[i + 2:i + 2 + n]
        names.append(APPLE_TYPES.get(t, f"type 0x{t:02X}"))
        if t == 0x02 and n == 0x15 and len(v) == 21:
            beacon = {"beacon": (f"iBeacon {uuid.UUID(bytes=bytes(v[:16]))} major {v[16] << 8 | v[17]} "
                                 f"minor {v[18] << 8 | v[19]}"),
                      "power_1m": int.from_bytes(v[20:21], "little", signed=True)}
        i += 2 + n
    return names, beacon


def _eddystone(d):
    if not d:
        return "Eddystone (empty)", {}
    kind, power = d[0], (int.from_bytes(d[1:2], "little", signed=True) if len(d) > 1 else None)
    if kind == 0x00 and len(d) >= 18:
        text = f"Eddystone-UID {d[2:12].hex().upper()} / {d[12:18].hex().upper()}, {power} dBm at 0 m"
        return text, {"beacon": text, "power_0m": power}
    if kind == 0x10 and len(d) >= 3:
        url = EDDYSTONE_URL_SCHEMES[d[2]] if d[2] < 4 else ""
        url += "".join(EDDYSTONE_URL_CODES[c] if c < len(EDDYSTONE_URL_CODES) else chr(c) for c in d[3:])
        text = f"Eddystone-URL {url}, {power} dBm at 0 m"
        return text, {"beacon": text, "power_0m": power, "uri": url}
    if kind == 0x20 and len(d) >= 14:
        mv, temp = d[2] << 8 | d[3], int.from_bytes(d[4:6], "big", signed=True) / 256
        count, secs = int.from_bytes(d[6:10], "big"), int.from_bytes(d[10:14], "big") / 10
        return f"Eddystone-TLM battery {mv} mV, {temp:.1f} °C, {count} packets, up {secs:.0f} s", {}
    if kind == 0x30:
        return f"Eddystone-EID {d[2:10].hex().upper()}", {"beacon": "Eddystone-EID", "power_0m": power}
    return f"Eddystone frame 0x{kind:02X}", {}


def parse(data):
    """AD structures of AdvData (or ScanRspData, ACAD). Returns (structures, errors).

    A Length of zero marks the start of the non-significant (zero padding) part.
    """
    out, errors, i = [], [], 0
    data = bytes(data)
    while i < len(data):
        n = data[i]
        if n == 0:
            break
        if i + 1 + n > len(data):
            errors.append(f"AD structure at octet {i} runs past the end ({n} > {len(data) - i - 1})")
            break
        t, d = data[i + 1], data[i + 2:i + 1 + n]
        name = ad_type_names().get(t, f"AD type 0x{t:02X}")
        try:
            text, fields = _decode(t, d)
        except Exception as exc:              # a malformed value must not stop the scan
            text, fields = f"{_hex(d)} (could not decode: {exc})", {}
        out.append(ADStructure(t, d, name, text, fields))
        i += 1 + n
    return out, errors


def summary(structures):
    """Merge the fields of all structures: later lists extend, a complete name wins."""
    s = {}
    for a in structures:
        for k, v in a.fields.items():
            if k in ("services", "solicited", "apple"):
                s.setdefault(k, [])
                s[k] += [x for x in v if x not in s[k]]
            elif k == "service_data":
                s.setdefault(k, {}).update(v)
            elif k == "name":
                if a.fields.get("name_complete") or not s.get("name_complete"):
                    s["name"], s["name_complete"] = v, a.fields.get("name_complete")
            elif k != "name_complete":
                s[k] = v
    return s
