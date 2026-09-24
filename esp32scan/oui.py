"""MAC address vendor lookup from the IEEE registries (see tools/update-oui.py).

Only universally administered addresses carry an OUI. A set U/L bit (0x02 in
the first octet) marks a locally administered address: randomized client
MACs, and the extra virtual BSSIDs an access point makes for each additional
SSID. Those have no vendor.
"""

import gzip
from functools import lru_cache
from pathlib import Path

DB = Path(__file__).resolve().parent / "ieee" / "oui.tsv.gz"


@lru_cache(maxsize=None)
def _table():
    out = {}
    try:
        with gzip.open(DB, "rt", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                prefix, _, org = line.rstrip("\n").partition("\t")
                out[prefix] = org
    except FileNotFoundError:
        pass
    return out


def _hex(mac):
    return mac.replace(":", "").replace("-", "").upper()


def is_local(mac):
    """Locally administered (U/L bit set)."""
    return bool(int(_hex(mac)[:2], 16) & 0x02)


def vendor(mac):
    """Organization for a universally administered address, else None.
    MA-S (36-bit) and MA-M (28-bit) blocks win over the MA-L (24-bit) block
    they were carved from."""
    h = _hex(mac)
    if len(h) < 6 or is_local(mac):
        return None
    t = _table()
    for n in (9, 7, 6):
        org = t.get(h[:n])
        if org:
            return org
    return None
