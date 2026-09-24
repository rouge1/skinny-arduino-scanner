#!/usr/bin/env python3
"""Rebuild esp32scan/ieee/oui.tsv.gz from the IEEE registration authority's
public registries:

  MA-L (24-bit prefix)  https://standards-oui.ieee.org/oui/oui.csv
  MA-M (28-bit prefix)  https://standards-oui.ieee.org/oui28/mam.csv
  MA-S (36-bit prefix)  https://standards-oui.ieee.org/oui36/oui36.csv

Output: one "HEXPREFIX<TAB>Organization" line per assignment, where the
prefix is 6, 7 or 9 hex digits. esp32scan.oui looks up the longest match.

Usage: .venv/bin/python tools/update-oui.py
"""
import csv
import gzip
import io
import sys
import urllib.request
from datetime import date
from pathlib import Path

SOURCES = (
    "https://standards-oui.ieee.org/oui/oui.csv",
    "https://standards-oui.ieee.org/oui28/mam.csv",
    "https://standards-oui.ieee.org/oui36/oui36.csv",
)
OUT = Path(__file__).resolve().parent.parent / "esp32scan" / "ieee" / "oui.tsv.gz"


def fetch(url):
    # IEEE's server rejects urllib's default User-Agent.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (esp32-ai update-oui)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def main():
    entries = {}
    for url in SOURCES:
        text = fetch(url)
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            prefix = row["Assignment"].strip().upper()
            org = " ".join(row["Organization Name"].split())
            if prefix and org:
                entries[prefix] = org
                n += 1
        print(f"{url}: {n} assignments", file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(f"# IEEE MA-L/MA-M/MA-S registries, downloaded {date.today()} by tools/update-oui.py\n")
        for prefix in sorted(entries):
            f.write(f"{prefix}\t{entries[prefix]}\n")
    print(f"wrote {len(entries)} prefixes to {OUT} ({OUT.stat().st_size // 1024} KB)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
