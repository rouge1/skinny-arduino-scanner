"""SQLite capture log shared by the GUI and serial-monitor.py.

Schema: one row in `sessions` per connection, one row in `scans` per
completed scan, one row in `entries` per network/device in that scan.

For WiFi entries `identifier` is the BSSID and `name` the SSID; for BLE
entries `identifier` is the device address. BLE vendor / TX power / service
UUID go in `extra` as JSON. (The first session in the db, recorded on the Mac
before BSSIDs were reported, has the SSID in `identifier`.)
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .protocol import MODE_LABELS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    mode TEXT,
    port TEXT,
    started_at TEXT,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS scans(
    id INTEGER PRIMARY KEY,
    session_id INTEGER,
    kind TEXT,
    scan_index INTEGER,
    captured_at TEXT,
    n_entries INTEGER
);
CREATE TABLE IF NOT EXISTS entries(
    id INTEGER PRIMARY KEY,
    session_id INTEGER,
    scan_id INTEGER,
    captured_at TEXT,
    kind TEXT,
    rssi INTEGER,
    identifier TEXT,
    name TEXT,
    channel INTEGER,
    security TEXT
);
"""


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class Store:
    def __init__(self, db_path, mode, port):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(entries)")}
        if "extra" not in cols:
            self.db.execute("ALTER TABLE entries ADD COLUMN extra TEXT")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.name = f"{mode}-{stamp}-{uuid.uuid4().hex[:6]}"
        cur = self.db.execute(
            "INSERT INTO sessions(name,mode,port,started_at) VALUES(?,?,?,?)",
            (self.name, MODE_LABELS.get(mode, mode), port, now_iso()),
        )
        self.session_id = cur.lastrowid

    def set_mode(self, mode):
        self.db.execute(
            "UPDATE sessions SET mode=? WHERE id=?",
            (MODE_LABELS.get(mode, mode), self.session_id),
        )

    def save_scan(self, kind, index, rows):
        """Write one completed scan. rows are the firmware's wifi/ble event
        dicts, each with a "_at" receive timestamp added by the caller."""
        self.db.execute("BEGIN")
        cur = self.db.execute(
            "INSERT INTO scans(session_id,kind,scan_index,captured_at,n_entries)"
            " VALUES(?,?,?,?,?)",
            (self.session_id, kind, index, now_iso(), len(rows)),
        )
        scan_id = cur.lastrowid
        for r in rows:
            if kind == "wifi":
                vals = (r["bssid"], r.get("ssid", ""), r.get("ch"), r.get("sec"), None)
            else:
                extra = {k: r[k] for k in ("mfr", "tx", "uuid") if k in r}
                vals = (r["addr"], r.get("name", ""), None, None,
                        json.dumps(extra) if extra else None)
            self.db.execute(
                "INSERT INTO entries(session_id,scan_id,captured_at,kind,rssi,"
                "identifier,name,channel,security,extra) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.session_id, scan_id, r["_at"], kind, r["rssi"], *vals),
            )
        self.db.execute("COMMIT")

    def close(self):
        self.db.execute(
            "UPDATE sessions SET ended_at=? WHERE id=?", (now_iso(), self.session_id)
        )
        self.db.close()
