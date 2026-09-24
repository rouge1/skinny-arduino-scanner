"""Panel (under the survey map) listing every network/device heard at one stop."""

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
                            Signal)
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QSlider, QTableView, QToolButton, QVBoxLayout, QWidget)

from ..decode import KIND_HELP
from . import theme as T
from .models import BEST_ROLE, RSSI_ROLE, SORT_ROLE
from .widgets import SignalDelegate, column_menu

NUM = Qt.AlignRight | Qt.AlignVCenter
LEFT = Qt.AlignLeft | Qt.AlignVCenter


def _details(d):
    if d["kind"] == "wifi":
        return ", ".join(x for x in (f"Ch {d['channel']}" if d["channel"] else "",
                                     d["security"]) if x)
    return d["info"]


# title, text(d), sort key(d), alignment, width, style ("" / "muted" / "mono"), hidden
COLS = [
    ("Kind", lambda d: "WiFi" if d["kind"] == "wifi" else "BT", None, LEFT, 52, "muted", False),
    ("Name", lambda d: d["name"] or ("(hidden)" if d["kind"] == "wifi" else ""),
     lambda d: (d["name"] or "\uffff").lower(), LEFT, 190, "", False),
    ("Signal (dBm)", lambda d: f"{d['rssi']:.0f}", lambda d: d["rssi"], LEFT, 150, "", False),
    ("Heard", lambda d: f"{d['heard']}/{d['scans']}", lambda d: d["heard"] / max(d["scans"], 1),
     NUM, 58, "", False),
    ("Loudest at", lambda d: "here" if d.get("here") else d.get("loudest_name", ""),
     lambda d: (not d.get("here"), d.get("loudest", 0)), LEFT, 110, "", False),
    ("Maker", lambda d: d["maker"], None, LEFT, 170, "muted", False),
    ("Details", _details, None, LEFT, 220, "", False),
    ("Address", lambda d: d["identifier"], None, LEFT, 170, "mono", False),
    ("Best", lambda d: str(d["best"]), lambda d: d["best"], NUM, 50, "", True),
    ("Ch", lambda d: str(d["channel"]) if d["channel"] else "", lambda d: d["channel"] or 0,
     NUM, 40, "", True),
    ("Security", lambda d: d["security"], None, LEFT, 85, "", True),
    ("Addr type", lambda d: d["addr_kind"], None, LEFT, 75, "muted", True),
    ("Services", lambda d: d["services"], None, LEFT, 160, "", True),
]
SIGNAL_COL = 2


class DeviceTable(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.mono = T.mono_font(9)
        self.tnum = T.tabular(T.ui_font(10.5))

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        d = self.rows[index.row()]
        title, text, sort, align, _, look, _ = COLS[index.column()]
        if role == Qt.DisplayRole:
            return text(d)
        if role == SORT_ROLE:
            return (sort or text)(d)
        if role == RSSI_ROLE:
            return round(d["rssi"]) if index.column() == SIGNAL_COL else None
        if role == BEST_ROLE:
            return d["best"] if index.column() == SIGNAL_COL else None
        if role == Qt.TextAlignmentRole:
            return int(align)
        if role == Qt.ForegroundRole:
            if title == "Loudest at":
                return QColor(T.ACCENT if d.get("here") else T.MUTED)
            if look in ("muted", "mono"):
                return QColor(T.MUTED)
        if role == Qt.FontRole:
            return self.mono if look == "mono" else self.tnum if align == NUM else None
        if role == Qt.ToolTipRole:
            if index.column() == SIGNAL_COL:
                return f"Average {d['rssi']:.0f} dBm at this stop, best {d['best']} dBm"
            if title == "Addr type":
                return KIND_HELP.get(d["addr_kind"])
            if title == "Heard":
                return f"Heard in {d['heard']} of the {d['scans']} scans at this stop"
            if title == "Loudest at":
                return (f"Strongest at stop {d.get('loudest', 0) + 1} over the whole walk, "
                        "so most likely located there")
            t = text(d)
            return t if len(t) > 25 else None
        return None


class KindFilter(QSortFilterProxyModel):
    def __init__(self, source):
        super().__init__()
        self.setSourceModel(source)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)
        self.kinds = {"wifi", "ble"}
        self.min_rssi = -100
        self.only_here = False

    def set_kind(self, kind, on):
        (self.kinds.add if on else self.kinds.discard)(kind)
        self.invalidateFilter()

    def set_min_rssi(self, v):
        self.min_rssi = v
        self.invalidateFilter()

    def set_only_here(self, on):
        self.only_here = on
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        d = self.sourceModel().rows[row]
        if d["kind"] not in self.kinds:
            return False
        if self.min_rssi > -100 and d["rssi"] < self.min_rssi:  # -100 = off
            return False
        if self.only_here and not d.get("here"):
            return False
        return super().filterAcceptsRow(row, parent)


def chip(text, tip):
    b = QToolButton(text=text, checkable=True, checked=True, toolTip=tip)
    b.setProperty("chip", True)
    b.setCursor(Qt.PointingHandCursor)
    return b


class StopDevicesPanel(QWidget):
    """show_stop() replaces the contents on every map click; clear() goes back
    to the empty state."""

    kinds_changed = Signal(set)  # the user toggled the WiFi/Bluetooth chips

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = DeviceTable()
        self.proxy = KindFilter(self.model)

        self.heading = QLabel(objectName="cardTitle")
        self.sub = QLabel(objectName="subheading")

        self.wifi_chip = chip("WiFi", "Show WiFi networks")
        self.wifi_chip.toggled.connect(lambda on: self._kind("wifi", on))
        self.ble_chip = chip("Bluetooth", "Show Bluetooth devices")
        self.ble_chip.toggled.connect(lambda on: self._kind("ble", on))
        self.filter = QLineEdit(placeholderText="Search name, address, maker")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumWidth(100)
        self.filter.setMaximumWidth(280)
        self.filter.textChanged.connect(self._text)
        QShortcut(QKeySequence.Find, self, activated=self.filter.setFocus)

        self.min_rssi = QSlider(Qt.Horizontal, minimum=-100, maximum=-30, value=-100)
        self.min_rssi.setMinimumWidth(70)
        self.min_rssi.setMaximumWidth(150)
        self.min_rssi.setToolTip("Minimum signal: hide anything weaker than this at this stop. Roughly: "
                                 "−60 or better is usually the same room, −80 or worse far away")
        self.min_rssi.valueChanged.connect(self._min_rssi)
        self.min_label = QLabel(objectName="fieldLabel")
        self.min_label.setMinimumWidth(64)
        self.only_here = QCheckBox("Strongest here only")
        self.only_here.setToolTip("Only devices whose strongest reading on the whole walk "
                                  "was at this stop, so most likely located here")
        self.only_here.toggled.connect(self._only_here)
        self.count = QLabel(objectName="count")

        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(self.wifi_chip)
        bar.addWidget(self.ble_chip)
        bar.addSpacing(6)
        bar.addWidget(self.filter, 2)
        bar.addSpacing(10)
        bar.addWidget(self.min_label)
        bar.addWidget(self.min_rssi, 1)
        bar.addWidget(self.only_here)
        bar.addStretch(1)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(SIGNAL_COL, Qt.DescendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QTableView.ScrollPerPixel)
        self.table.setItemDelegateForColumn(SIGNAL_COL, SignalDelegate(self.table))
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setHighlightSections(False)
        hdr.setStretchLastSection(True)
        for i, c in enumerate(COLS):
            self.table.setColumnWidth(i, c[4])
        column_menu(self.table, "stop_devices", [c[0] for c in COLS if c[6]])

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.heading)
        head.addWidget(self.sub, 1)
        head.addWidget(self.count)

        self.controls = QWidget()
        cl = QVBoxLayout(self.controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addLayout(bar)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        lay.setSpacing(10)
        lay.addLayout(head)
        lay.addWidget(self.controls)
        lay.addWidget(self.table, 1)
        self._min_rssi(self.min_rssi.value())
        self.clear()

    def _kind(self, kind, on):
        self.proxy.set_kind(kind, on)
        self._update_count()
        self.kinds_changed.emit(set(self.proxy.kinds))

    def _min_rssi(self, v):
        self.min_label.setText("Any signal" if v <= -100
                               else f"≥ {v} dBm".replace("-", "−"))
        self.proxy.set_min_rssi(v)
        self._update_count()

    def _only_here(self, on):
        self.proxy.set_only_here(on)
        self._update_count()

    def _text(self, text):
        self.proxy.setFilterFixedString(text)
        self._update_count()

    def _update_count(self):
        rows = self.model.rows
        nw = sum(1 for d in rows if d["kind"] == "wifi")
        self.wifi_chip.setText(f"WiFi  {nw}")
        self.ble_chip.setText(f"Bluetooth  {len(rows) - nw}")
        shown = self.proxy.rowCount()
        self.count.setText(f"{shown} shown" if shown != len(rows) else f"{shown} in total")

    def show_stop(self, number, point, devices):
        for d in devices:
            d["here"] = d.get("loudest") == number - 1
        self.heading.setText(f"Devices at {point.name}")
        when = f", captured at {point.started_at[11:16]}" if point.started_at else ""
        self.sub.setText(f"Stop {number}. Heard in {point.count('wifi')} WiFi and "
                         f"{point.count('ble')} Bluetooth scans{when}.")
        self.model.set_rows(devices)
        self.controls.setEnabled(True)
        self._update_count()

    def clear(self, hint="Click near a captured stop on the map to list what was heard there."):
        self.heading.setText("Devices")
        self.sub.setText(hint)
        self.model.set_rows([])
        self.controls.setEnabled(False)
        self.count.setText("")
