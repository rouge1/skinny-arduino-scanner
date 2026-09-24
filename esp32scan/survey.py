"""Floor-plan survey: routes, stored captures, per-stop values and the heat-map
interpolation. No Qt here; the GUI (gui/survey_tab.py) drives it.

A survey walks a route (routes/*.json: stops with pixel positions on a floor
plan image). At each stop the BOOT button starts and stops a capture; every
scan that ran entirely inside the capture (compared in the board's own clock)
belongs to that stop. Captures are kept in their own tables, independent of
the general capture log:

  surveys          one row per survey (route name, route JSON, floor plan)
  survey_points    one row per captured/skipped stop (redoing a stop replaces it)
  survey_readings  one row per network/device per scan at that stop
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import PROJECT_ROOT
from . import addata
from .decode import ble_notes, enrich, maker, services_text
from .protocol import parse_line

SCHEMA = """
CREATE TABLE IF NOT EXISTS surveys(
    id INTEGER PRIMARY KEY,
    name TEXT,
    route TEXT,
    floorplan TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS survey_points(
    id INTEGER PRIMARY KEY,
    survey_id INTEGER,
    stop INTEGER,
    name TEXT,
    x REAL,
    y REAL,
    status TEXT,
    started_at TEXT,
    ended_at TEXT,
    n_wifi INTEGER,
    n_ble INTEGER
);
CREATE TABLE IF NOT EXISTS survey_readings(
    id INTEGER PRIMARY KEY,
    point_id INTEGER,
    kind TEXT,
    scan INTEGER,
    identifier TEXT,
    name TEXT,
    rssi INTEGER,
    channel INTEGER,
    security TEXT,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS survey_readings_point ON survey_readings(point_id);
"""

FLOOR_DBM = -100  # value for "not heard at this stop"


# ---- routes ----------------------------------------------------------------

def load_route(path):
    path = Path(path)
    route = json.loads(path.read_text())
    plan = Path(route["floorplan"])
    if not plan.is_absolute():
        plan = PROJECT_ROOT / plan
    route["floorplan"] = str(plan)
    route["path"] = str(path)
    return route


def default_route():
    routes = sorted((PROJECT_ROOT / "routes").glob("*.json"))
    return load_route(routes[0]) if routes else None


# ---- captured data ---------------------------------------------------------

def reading(kind, row):
    """A firmware row (after decode.enrich) reduced to what a survey keeps."""
    if kind == "wifi":
        return {"identifier": row["bssid"], "name": row.get("ssid", ""), "rssi": row["rssi"],
                "channel": row.get("ch"), "security": row.get("sec"),
                "extra": {"vendor": row.get("vendor", "")}}
    return {"identifier": row["addr"], "name": row.get("name", ""), "rssi": row["rssi"],
            "channel": None, "security": None,
            "extra": {"kind": row.get("kind"), "maker": maker(row), "adv": row.get("adv", "")}}


@dataclass
class Scan:
    kind: str
    index: int
    readings: list


@dataclass
class Point:
    stop: int
    name: str
    x: float
    y: float
    status: str                      # "done" or "skipped"
    started_at: str = ""
    ended_at: str = ""
    scans: list = field(default_factory=list)

    def count(self, kind):
        return sum(1 for s in self.scans if s.kind == kind)


class SurveyStore:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, isolation_level=None, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def new_survey(self, route, suffix=""):
        name = f"{route['name']} {datetime.now():%Y-%m-%d %H:%M}{suffix}"
        stored = {k: v for k, v in route.items() if k != "path"}
        cur = self.db.execute(
            "INSERT INTO surveys(name,route,floorplan,created_at) VALUES(?,?,?,?)",
            (name, json.dumps(stored), route["floorplan"],
             datetime.now().astimezone().isoformat(timespec="seconds")))
        return cur.lastrowid

    def surveys(self):
        """[(id, name, points done, stops in route)], newest first."""
        out = []
        for sid, name, route in self.db.execute(
                "SELECT id, name, route FROM surveys ORDER BY id DESC"):
            done = self.db.execute(
                "SELECT COUNT(*) FROM survey_points WHERE survey_id=? AND status='done'",
                (sid,)).fetchone()[0]
            out.append((sid, name, done, len(json.loads(route)["stops"])))
        return out

    def route(self, survey_id):
        row = self.db.execute("SELECT route FROM surveys WHERE id=?", (survey_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_point(self, survey_id, p):
        self.db.execute("BEGIN")
        for (pid,) in self.db.execute(
                "SELECT id FROM survey_points WHERE survey_id=? AND stop=?",
                (survey_id, p.stop)).fetchall():
            self.db.execute("DELETE FROM survey_readings WHERE point_id=?", (pid,))
            self.db.execute("DELETE FROM survey_points WHERE id=?", (pid,))
        cur = self.db.execute(
            "INSERT INTO survey_points(survey_id,stop,name,x,y,status,started_at,ended_at,"
            "n_wifi,n_ble) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (survey_id, p.stop, p.name, p.x, p.y, p.status, p.started_at, p.ended_at,
             p.count("wifi"), p.count("ble")))
        pid = cur.lastrowid
        for s in p.scans:
            for r in s.readings:
                self.db.execute(
                    "INSERT INTO survey_readings(point_id,kind,scan,identifier,name,rssi,"
                    "channel,security,extra) VALUES(?,?,?,?,?,?,?,?,?)",
                    (pid, s.kind, s.index, r["identifier"], r["name"], r["rssi"],
                     r["channel"], r["security"], json.dumps(r["extra"])))
        self.db.execute("COMMIT")

    def points(self, survey_id):
        out = {}
        for (pid, stop, name, x, y, status, t0, t1) in self.db.execute(
                "SELECT id,stop,name,x,y,status,started_at,ended_at FROM survey_points "
                "WHERE survey_id=?", (survey_id,)).fetchall():
            scans = {}
            for kind, scan, ident, rname, rssi, ch, sec, extra in self.db.execute(
                    "SELECT kind,scan,identifier,name,rssi,channel,security,extra "
                    "FROM survey_readings WHERE point_id=? ORDER BY id", (pid,)):
                s = scans.setdefault((kind, scan), Scan(kind, scan, []))
                s.readings.append({"identifier": ident, "name": rname, "rssi": rssi,
                                   "channel": ch, "security": sec,
                                   "extra": json.loads(extra) if extra else {}})
            # A scan that heard nothing has no readings; n_wifi/n_ble aren't
            # needed back because empty scans don't change any value.
            out[stop] = Point(stop, name, x, y, status, t0, t1, list(scans.values()))
        return out


# ---- importing a board's standalone log ------------------------------------

def import_log(lines, route):
    """Replay a board's survey log (captured on a power bank) into Points.

    The log holds, per power-up, a boot line followed by the button events and
    the scans that started during a capture, in the same JSON as the live
    protocol. Accepted captures and skips take the route's stops in order;
    rejected captures (too short) don't. A scan counts for a capture when it
    started at or after the start press and finished by the stop press (board
    millis(), which restart at every boot; a capture cut off by a reboot is
    dropped). There's no wall clock on a power bank, so started_at/ended_at
    are left empty.

    Returns (points, notes): {stop index: Point}, and warnings for the user.
    """
    stops = route["stops"]
    points, notes = {}, []
    stop = 0
    mark = None
    pending = {"wifi": [], "ble": []}
    extra_stops = 0

    def take(kind):
        rows, pending[kind] = pending[kind], []
        return rows

    for raw in lines:
        ev = parse_line(raw)
        if ev is None:
            continue
        kind = ev["ev"]
        if kind == "boot":
            if mark:
                notes.append(f"A capture at stop {stop + 1} was cut off by a restart and dropped.")
            mark = None
            pending = {"wifi": [], "ble": []}
        elif kind == "scan_start":
            pending[ev["kind"]] = []
        elif kind in ("wifi", "ble"):
            pending[kind].append(enrich(kind, ev))
        elif kind == "scan_done":
            rows = take(ev["kind"])
            if mark and ev.get("t0", -1) >= mark["t0"]:
                mark["scans"].append((ev, Scan(ev["kind"], ev["scan"],
                                               [reading(ev["kind"], r) for r in rows])))
        elif kind == "button":
            action = ev["action"]
            if action == "start":
                mark = {"t0": ev["t"], "scans": []}
            elif action in ("stop", "skip"):
                scans = []
                if action == "stop" and mark:
                    scans = [sc for done, sc in mark["scans"] if done.get("t1", 0) <= ev["t"]]
                mark = None
                if stop >= len(stops):
                    extra_stops += 1
                    continue
                s = stops[stop]
                status = "done" if action == "stop" and scans else "skipped"
                if action == "stop" and not scans:
                    notes.append(f"Stop {stop + 1} ({s['name']}) had no complete scan; "
                                 "marked skipped.")
                points[stop] = Point(stop, s["name"], s["x"], s["y"], status, "", "", scans)
                stop += 1
            elif action == "reject":
                mark = None
    if extra_stops:
        notes.append(f"{extra_stops} capture(s) beyond the route's last stop were ignored.")
    if mark:
        notes.append(f"The log ends during a capture at stop {stop + 1}; it was dropped.")
    return points, notes


# ---- per-stop values -------------------------------------------------------

# layer key -> (label, scan kind, unit, needs a target)
LAYERS = {
    "wifi_ssid": ("WiFi: coverage of one network (SSID)", "wifi", "dBm", True),
    "wifi_best": ("WiFi: strongest signal, any network", "wifi", "dBm", False),
    "wifi_ap": ("WiFi: one access point (BSSID)", "wifi", "dBm", True),
    "wifi_count": ("WiFi: access points heard", "wifi", "count", False),
    "ble_dev": ("Bluetooth: one device", "ble", "dBm", True),
    "ble_count": ("Bluetooth: devices heard", "ble", "count", False),
    "all_count": ("WiFi + Bluetooth: everything heard", "all", "count", False),
}


def _match(layer, target):
    if layer == "wifi_ssid":
        return lambda r: r["name"] == target
    if layer in ("wifi_ap", "ble_dev"):
        return lambda r: r["identifier"] == target
    return lambda r: True


def point_value(point, layer, target=None):
    """The stop's value for a layer, or None if the stop has no scans of that
    kind. Signal layers: per scan the strongest matching reading, averaged over
    the scans that heard it; FLOOR_DBM if none did. Count layers: distinct
    identifiers per scan, averaged over scans (so longer captures don't count
    more). "all_count" adds the WiFi and Bluetooth averages."""
    _, kind, unit, _ = LAYERS[layer]
    if kind == "all":
        parts = [point_value(point, k) for k in ("wifi_count", "ble_count")]
        parts = [v for v in parts if v is not None]
        return float(sum(parts)) if parts else None
    scans = [s for s in point.scans if s.kind == kind]
    if point.status != "done" or not scans:
        return None
    if unit == "count":
        return float(np.mean([len({r["identifier"] for r in s.readings}) for s in scans]))
    match = _match(layer, target)
    per_scan = [max((r["rssi"] for r in s.readings if match(r)), default=None) for s in scans]
    heard = [v for v in per_scan if v is not None]
    return float(np.mean(heard)) if heard else float(FLOOR_DBM)


def targets(points, layer):
    """Choices for a layer that needs a target: [(key, label)], most widely
    heard first, then strongest."""
    _, kind, _, needs = LAYERS[layer]
    if not needs:
        return []
    stats = {}  # key -> [stops heard at, best rssi, label]
    for p in points.values():
        seen = {}
        for s in p.scans:
            if s.kind != kind:
                continue
            for r in s.readings:
                key = r["name"] if layer == "wifi_ssid" else r["identifier"]
                if layer == "wifi_ssid" and not key:
                    continue  # hidden networks have no SSID to group by
                seen[key] = max(seen.get(key, -999), r["rssi"])
                if key not in stats:
                    stats[key] = [0, -999, _label(layer, r)]
        for key, best in seen.items():
            stats[key][0] += 1
            stats[key][1] = max(stats[key][1], best)
    order = sorted(stats.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
    return [(k, f"{v[2]}  ·  {v[0]} stops, best {v[1]} dBm") for k, v in order]


def _label(layer, r):
    if layer == "wifi_ssid":
        return r["name"]
    if layer == "wifi_ap":
        vendor = r["extra"].get("vendor") or ""
        return f"{r['name'] or '(hidden)'}  {r['identifier']}  {vendor}".rstrip()
    extra = r["extra"]
    who = r["name"] or extra.get("maker") or "(unnamed)"
    return f"{who}  {r['identifier']}  {extra.get('kind') or ''}".rstrip()


# ---- what a stop heard -----------------------------------------------------

def stop_devices(point):
    """Every network/device heard at a stop, one dict per identifier:
    kind ("wifi"/"ble"), identifier, name, maker (OUI vendor for WiFi), rssi
    (average over the scans that heard it), best, heard (scans that heard it),
    scans (scans of that kind at the stop), plus channel/security for WiFi and
    addr_kind/services/info (decoded from the strongest advertisement) for BLE."""
    n_scans = {"wifi": point.count("wifi"), "ble": point.count("ble")}
    acc = {}
    for s in point.scans:
        for r in s.readings:
            key = (s.kind, r["identifier"])
            a = acc.setdefault(key, {"kind": s.kind, "values": [], "best": None, "name": ""})
            a["values"].append(r["rssi"])
            if a["best"] is None or r["rssi"] > a["best"]["rssi"]:
                a["best"] = r
            if r["name"]:
                a["name"] = r["name"]
    out = []
    for (kind, ident), a in acc.items():
        r, extra = a["best"], a["best"]["extra"]
        d = {"kind": kind, "identifier": ident, "name": a["name"],
             "rssi": float(np.mean(a["values"])), "best": max(a["values"]),
             "heard": len(a["values"]), "scans": n_scans[kind]}
        if kind == "wifi":
            d.update(maker=extra.get("vendor", ""), channel=r["channel"],
                     security=r["security"] or "", addr_kind="", services="", info="")
        else:
            info = addata.summary(addata.parse(bytes.fromhex(extra.get("adv", "")))[0])
            d.update(maker=extra.get("maker", ""), channel=None, security="",
                     addr_kind=extra.get("kind") or "", services=services_text(info),
                     info=ble_notes(info))
        out.append(d)
    out.sort(key=lambda d: -d["rssi"])
    return out


def loudest_stops(points):
    """{(kind, identifier): (stop, average rssi)} for the stop where each
    network/device was strongest over the whole survey. A device is most
    likely nearest the stop where it was loudest (RPA addresses rotate about
    every 15 min, so a phone can show up under two addresses on a long walk)."""
    out = {}
    for stop, p in points.items():
        if p.status != "done":
            continue
        for d in stop_devices(p):
            key = (d["kind"], d["identifier"])
            if key not in out or d["rssi"] > out[key][1]:
                out[key] = (stop, d["rssi"])
    return out


# ---- interpolation ---------------------------------------------------------

def idw_grid(xs, ys, values, width, height, step=4, power=2.0, reach=200.0):
    """Inverse-distance-weighted surface over a width x height image, sampled
    every `step` pixels. Returns (z, alpha): alpha fades from 1 to 0 over the
    last 35 % of `reach` from the nearest stop, so the map stays blank where
    there's no data to support it."""
    xs, ys, values = (np.asarray(a, dtype=float) for a in (xs, ys, values))
    gx = np.arange(step / 2, width, step)
    gy = np.arange(step / 2, height, step)
    X, Y = np.meshgrid(gx, gy)
    d = np.hypot(X[..., None] - xs, Y[..., None] - ys)
    w = 1.0 / np.maximum(d, 1.0) ** power
    z = (w * values).sum(-1) / w.sum(-1)
    fade = max(reach * 0.35, 1.0)
    alpha = np.clip((reach - d.min(-1)) / fade, 0.0, 1.0)
    return z, alpha


SIGNAL_STOPS = ["#b2182b", "#ef6548", "#fdd049", "#91cf60", "#1a9850"]   # weak -> strong
COUNT_STOPS = ["#0d0887", "#6a00a8", "#b12a90", "#e16462", "#fca636", "#f0f921"]  # plasma


def _rgb(hexes):
    return np.array([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in hexes], dtype=float)


def colorize(z, alpha, vmin, vmax, stops, opacity=0.62):
    """RGBA uint8 image for z on a linear ramp through `stops`."""
    t = np.clip((z - vmin) / max(vmax - vmin, 1e-9), 0, 1)
    rgb = _rgb(stops)
    pos = t * (len(rgb) - 1)
    i = np.minimum(pos.astype(int), len(rgb) - 2)
    f = (pos - i)[..., None]
    col = rgb[i] * (1 - f) + rgb[i + 1] * f
    a = (alpha * opacity * 255)[..., None]
    return np.concatenate([col, a], axis=-1).astype(np.uint8)


def value_range(layer, values):
    """Colour scale for a layer: fixed for signal, 0..max for counts."""
    if LAYERS[layer][2] == "dBm":
        return -90.0, -40.0
    return 0.0, max([v for v in values if v is not None] + [1.0])
