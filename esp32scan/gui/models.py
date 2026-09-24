"""Table models that accumulate devices across scans."""

import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from ..decode import KIND_HELP, ble_notes, maker, services_text

HISTORY_LEN = 720  # RSSI samples kept per device (~2 h of 10 s cycles)
SORT_ROLE = Qt.UserRole
RSSI_ROLE = Qt.UserRole + 1


class Record:
    __slots__ = ("key", "d", "rssi", "best", "first", "last", "seen", "scan", "history")

    def __init__(self, key, d, now, scan):
        self.key = key
        self.d = d
        self.rssi = d["rssi"]
        self.best = d["rssi"]
        self.first = now
        self.last = now
        self.seen = 0
        self.scan = scan
        self.history = deque(maxlen=HISTORY_LEN)

    def update(self, d, now, scan):
        # Advertisements alternate (e.g. with and without a scan response), so
        # keep what earlier ones told us: the name, and any decoded fields.
        if self.d is not d:
            if not d.get("name") and self.d.get("name"):
                d = dict(d, name=self.d["name"])
            if "info" in d:
                d["info"] = {**self.d.get("info", {}), **d["info"]}
                if not d.get("company") and self.d.get("company"):
                    d["company"] = self.d["company"]
        self.d = d
        self.rssi = d["rssi"]
        self.best = max(self.best, d["rssi"])
        self.last = now
        self.seen += 1
        self.scan = scan
        self.history.append((now, d["rssi"]))


def _age(t):
    s = int(time.time() - t)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


def _clock(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


class Col:
    def __init__(self, title, text, sort=None, align=Qt.AlignLeft, rssi=False,
                 width=80, stretch=False, tip=None):
        self.title = title
        self.tip = tip
        self.width = width
        self.stretch = stretch
        self.text = text
        self.sort = sort or text
        self.align = align | Qt.AlignVCenter
        self.rssi = rssi


NUM = Qt.AlignRight

WIFI_COLS = [
    Col("SSID", lambda r: r.d.get("ssid") or "(hidden)",
        sort=lambda r: (r.d.get("ssid") or "\uffff").lower(), width=220, stretch=True),
    Col("BSSID", lambda r: r.d["bssid"], width=150),
    Col("Vendor", lambda r: r.d.get("vendor", ""), width=160),
    Col("Ch", lambda r: str(r.d.get("ch", "")), sort=lambda r: r.d.get("ch", 0),
        align=NUM, width=50),
    Col("Security", lambda r: r.d.get("sec", ""), width=90),
    Col("Signal", lambda r: f"{r.rssi} dBm", sort=lambda r: r.rssi, rssi=True, width=150),
    Col("Best", lambda r: str(r.best), sort=lambda r: r.best, align=NUM),
    Col("Seen", lambda r: str(r.seen), sort=lambda r: r.seen, align=NUM),
    Col("Last seen", lambda r: _age(r.last), sort=lambda r: r.last, width=90),
    Col("First seen", lambda r: _clock(r.first), sort=lambda r: r.first, width=90),
]

BLE_COLS = [
    Col("Address", lambda r: r.d["addr"], width=140),
    Col("Type", lambda r: r.d.get("kind", ""), width=65,
        tip=lambda r: KIND_HELP.get(r.d.get("kind"))),
    Col("Name", lambda r: r.d.get("name", ""),
        sort=lambda r: (r.d.get("name") or "\uffff").lower(), width=170),
    Col("Maker", lambda r: maker(r.d), width=170),
    Col("Signal", lambda r: f"{r.rssi} dBm", sort=lambda r: r.rssi, rssi=True, width=150),
    Col("Best", lambda r: str(r.best), sort=lambda r: r.best, align=NUM, width=55),
    Col("TX", lambda r: str(r.d["info"]["tx_power"]) if "tx_power" in r.d.get("info", {}) else "",
        sort=lambda r: r.d.get("info", {}).get("tx_power", -999), align=NUM, width=45),
    Col("Seen", lambda r: str(r.seen), sort=lambda r: r.seen, align=NUM, width=50),
    Col("Last seen", lambda r: _age(r.last), sort=lambda r: r.last, width=80),
    Col("First seen", lambda r: _clock(r.first), sort=lambda r: r.first, width=80),
    Col("Services", lambda r: services_text(r.d.get("info", {})), width=200),
    Col("Info", lambda r: ble_notes(r.d.get("info", {})), width=300, stretch=True),
]


class DeviceModel(QAbstractTableModel):
    def __init__(self, cols, key_field):
        super().__init__()
        self.cols = cols
        self.key_field = key_field
        self.rows = []
        self.index_of = {}
        self.scan = 0  # index of the latest completed scan
        self.last_seen_col = next(i for i, c in enumerate(cols) if c.title == "Last seen")

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.cols[section].title
        return None

    def is_current(self, row):
        return self.rows[row].scan == self.scan

    def data(self, index, role=Qt.DisplayRole):
        r = self.rows[index.row()]
        c = self.cols[index.column()]
        if role == Qt.DisplayRole:
            return c.text(r)
        if role == SORT_ROLE:
            return c.sort(r)
        if role == RSSI_ROLE:
            return r.rssi if c.rssi else None
        if role == Qt.TextAlignmentRole:
            return int(c.align)
        if role == Qt.ForegroundRole and r.scan != self.scan:
            return QColor(128, 128, 128)
        if role == Qt.ToolTipRole:
            if r.scan != self.scan:
                return "Not seen in the latest scan"
            if c.tip:
                return c.tip(r)
            text = c.text(r)
            return text if len(text) > 30 else None
        return None

    def apply_scan(self, index, rows):
        now = time.time()
        self.scan = index
        new = []
        for d in rows:
            key = d[self.key_field]
            i = self.index_of.get(key)
            if i is None:
                rec = Record(key, d, now, index)
                rec.update(d, now, index)
                new.append(rec)
            else:
                self.rows[i].update(d, now, index)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.rows) - 1, len(self.cols) - 1))
        if new:
            start = len(self.rows)
            self.beginInsertRows(QModelIndex(), start, start + len(new) - 1)
            for rec in new:
                self.index_of[rec.key] = len(self.rows)
                self.rows.append(rec)
            self.endInsertRows()

    def tick(self):
        """Refresh the relative 'Last seen' column."""
        if self.rows:
            col = self.last_seen_col
            self.dataChanged.emit(self.index(0, col), self.index(len(self.rows) - 1, col))

    def current(self):
        return [r for r in self.rows if r.scan == self.scan]

    def clear(self):
        self.beginResetModel()
        self.rows = []
        self.index_of = {}
        self.scan = 0
        self.endResetModel()


class DeviceFilter(QSortFilterProxyModel):
    def __init__(self, source):
        super().__init__()
        self.setSourceModel(source)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.only_current = False

    def set_only_current(self, on):
        self.only_current = on
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if self.only_current and not self.sourceModel().is_current(row):
            return False
        return super().filterAcceptsRow(row, parent)

    def records(self, proxy_rows):
        src = self.sourceModel()
        return [src.rows[self.mapToSource(self.index(r, 0)).row()] for r in proxy_rows]
