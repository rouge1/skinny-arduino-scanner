"""Window listing every network/device heard at one survey stop."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QSlider, QTableView, QVBoxLayout)

from ..decode import KIND_HELP
from .models import RSSI_ROLE, SORT_ROLE
from .widgets import RssiDelegate

NUM = Qt.AlignRight | Qt.AlignVCenter
LEFT = Qt.AlignLeft | Qt.AlignVCenter

# title, text(d), sort key(d), alignment, width
COLS = [
    ("Type", lambda d: "WiFi" if d["kind"] == "wifi" else "Bluetooth", None, LEFT, 80),
    ("Name", lambda d: d["name"] or ("(hidden)" if d["kind"] == "wifi" else ""),
     lambda d: (d["name"] or "￿").lower(), LEFT, 190),
    ("Address", lambda d: d["identifier"], None, LEFT, 140),
    ("Maker", lambda d: d["maker"], None, LEFT, 170),
    ("Signal", lambda d: f"{d['rssi']:.0f} dBm", lambda d: d["rssi"], LEFT, 140),
    ("Best", lambda d: str(d["best"]), lambda d: d["best"], NUM, 50),
    ("Heard", lambda d: f"{d['heard']}/{d['scans']}", lambda d: d["heard"] / max(d["scans"], 1),
     NUM, 55),
    ("Loudest at", lambda d: "here" if d.get("here") else d.get("loudest_name", ""),
     lambda d: (not d.get("here"), d.get("loudest", 0)), LEFT, 120),
    ("Ch", lambda d: str(d["channel"]) if d["channel"] else "", lambda d: d["channel"] or 0,
     NUM, 40),
    ("Security", lambda d: d["security"], None, LEFT, 85),
    ("Addr type", lambda d: d["addr_kind"], None, LEFT, 75),
    ("Services", lambda d: d["services"], None, LEFT, 160),
    ("Info", lambda d: d["info"], None, LEFT, 200),
]
SIGNAL_COL = 4


class DeviceTable(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []

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
        title, text, sort, align, _ = COLS[index.column()]
        if role == Qt.DisplayRole:
            return text(d)
        if role == SORT_ROLE:
            return (sort or text)(d)
        if role == RSSI_ROLE:
            return round(d["rssi"]) if index.column() == SIGNAL_COL else None
        if role == Qt.TextAlignmentRole:
            return int(align)
        if role == Qt.ForegroundRole and title == "Loudest at" and d.get("here"):
            return QColor("#3fb950")
        if role == Qt.ToolTipRole:
            if title == "Addr type":
                return KIND_HELP.get(d["addr_kind"])
            if title == "Heard":
                return f"Heard in {d['heard']} of the {d['scans']} scans at this stop"
            if title == "Loudest at":
                return ("The stop where this was strongest over the whole walk: "
                        "most likely where it is")
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


class StopDevicesWindow(QDialog):
    """Non-modal; show_stop() replaces the contents on every map click."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Devices at stop")
        self.resize(1250, 620)
        self.model = DeviceTable()
        self.proxy = KindFilter(self.model)

        self.header = QLabel(textFormat=Qt.RichText)
        self.header.setStyleSheet("font-size: 13pt;")
        self.wifi_box = QCheckBox("WiFi", checked=True)
        self.wifi_box.toggled.connect(lambda on: self._kind("wifi", on))
        self.ble_box = QCheckBox("Bluetooth", checked=True)
        self.ble_box.toggled.connect(lambda on: self._kind("ble", on))
        self.filter = QLineEdit(placeholderText="Filter…  (name, address, maker, info)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._text)
        self.count = QLabel()

        self.min_rssi = QSlider(Qt.Horizontal, minimum=-100, maximum=-30, value=-100)
        self.min_rssi.setFixedWidth(220)
        self.min_rssi.setToolTip("Hide anything weaker than this at this stop. Roughly: "
                                 "-60 or better is usually the same room, -80 or worse far away")
        self.min_rssi.valueChanged.connect(self._min_rssi)
        self.min_label = QLabel()
        self.min_label.setMinimumWidth(110)
        self.only_here = QCheckBox("Only loudest here")
        self.only_here.setToolTip("Only devices whose strongest reading on the whole walk "
                                  "was at this stop, i.e. most likely located here")
        self.only_here.toggled.connect(self._only_here)

        bar = QHBoxLayout()
        bar.addWidget(self.wifi_box)
        bar.addWidget(self.ble_box)
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.count)
        bar2 = QHBoxLayout()
        bar2.addWidget(QLabel("Min signal"))
        bar2.addWidget(self.min_rssi)
        bar2.addWidget(self.min_label)
        bar2.addWidget(self.only_here)
        bar2.addStretch(1)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(SIGNAL_COL, Qt.DescendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setWordWrap(False)
        self.table.setItemDelegateForColumn(SIGNAL_COL, RssiDelegate(self.table))
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        for i, c in enumerate(COLS):
            self.table.setColumnWidth(i, c[4])

        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addLayout(bar)
        lay.addLayout(bar2)
        lay.addWidget(self.table, 1)
        self._min_rssi(self.min_rssi.value())

    def _kind(self, kind, on):
        self.proxy.set_kind(kind, on)
        self._update_count()

    def _min_rssi(self, v):
        self.min_label.setText("off (all)" if v <= -100 else f"≥ {v} dBm")
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
        self.count.setText(f"{nw} WiFi · {len(rows) - nw} Bluetooth · "
                           f"showing {self.proxy.rowCount()}")

    def show_stop(self, number, point, devices):
        for d in devices:
            d["here"] = d.get("loudest") == number - 1
        self.setWindowTitle(f"Devices at {point.name}")
        when = f" · captured {point.started_at[11:19]}" if point.started_at else ""
        self.header.setText(f"<b>{number}. {point.name}</b> · {point.count('wifi')} WiFi + "
                            f"{point.count('ble')} Bluetooth scans{when}")
        self.model.set_rows(devices)
        self._update_count()
        self.show()
        self.raise_()
