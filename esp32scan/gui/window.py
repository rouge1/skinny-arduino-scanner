"""Main window."""

import csv
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QPlainTextEdit, QPushButton, QSplitter, QTableView,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import DEFAULT_DB
from ..decode import ble_details
from ..link import list_ports
from ..protocol import MODE_LABELS
from .survey_tab import SurveyTab
from .models import BLE_COLS, WIFI_COLS, DeviceFilter, DeviceModel
from .widgets import ChannelMap, RssiDelegate, SignalHistory

TOP_N = 6  # devices charted when nothing is selected


class DevicePane(QWidget):
    """Filter bar + sortable table + chart tabs for one kind of device."""

    def __init__(self, model, charts):
        super().__init__()
        self.model = model
        self.proxy = DeviceFilter(model)

        self.filter = QLineEdit(placeholderText="Filter…  (matches any column)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.only_current = QCheckBox("Only in latest scan")
        self.only_current.toggled.connect(self.proxy.set_only_current)
        self.count = QLabel()

        bar = QHBoxLayout()
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.only_current)
        bar.addWidget(self.count)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        signal_col = next(i for i, c in enumerate(model.cols) if c.rssi)
        self.table.sortByColumn(signal_col, Qt.DescendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setWordWrap(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        for i, c in enumerate(model.cols):
            self.table.setColumnWidth(i, c.width)
            if c.stretch:
                hdr.setSectionResizeMode(i, QHeaderView.Stretch)
            if c.rssi:
                self.table.setItemDelegateForColumn(i, RssiDelegate(self.table))

        self.charts = QTabWidget()
        self.charts.setDocumentMode(True)
        for title, w in charts:
            self.charts.addTab(w, title)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.charts)
        split.setSizes([420, 280])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(bar)
        lay.addWidget(split, 1)

    def selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return self.proxy.records(rows)

    def chart_records(self):
        """Selected devices, or the strongest current ones if none selected."""
        sel = self.selected()
        if sel:
            return sel
        return sorted(self.model.current(), key=lambda r: r.rssi, reverse=True)[:TOP_N]

    def update_count(self):
        total = self.model.rowCount()
        cur = len(self.model.current())
        shown = self.proxy.rowCount()
        text = f"{cur} in latest scan · {total} total"
        if shown != total:
            text += f" · {shown} shown"
        self.count.setText(text)


class MainWindow(QMainWindow):
    def __init__(self, port=None, mode="both", db_path=DEFAULT_DB, record=True):
        super().__init__()
        self.setWindowTitle("ESP32 WiFi + Bluetooth Scanner")
        self.resize(1280, 820)
        self.db_path = db_path
        self.worker = None
        self.mode = mode
        self.paused = False

        self.wifi_model = DeviceModel(WIFI_COLS, "bssid")
        self.ble_model = DeviceModel(BLE_COLS, "addr")

        self.channel_map = ChannelMap()
        self.wifi_history = SignalHistory(lambda r: r.d.get("ssid") or r.d["bssid"])
        self.ble_history = SignalHistory(lambda r: r.d.get("name") or r.d["addr"])

        self.wifi = DevicePane(self.wifi_model,
                               [("Channels", self.channel_map),
                                ("Signal history", self.wifi_history)])
        self.ble_details = QPlainTextEdit(readOnly=True)
        self.ble_details.setStyleSheet("font-family: monospace;")
        self.ble_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.ble = DevicePane(self.ble_model, [("Signal history", self.ble_history),
                                                  ("Details", self.ble_details)])
        for pane in (self.wifi, self.ble):
            pane.table.selectionModel().selectionChanged.connect(self.refresh_charts)
            pane.proxy.rowsInserted.connect(pane.update_count)
            pane.proxy.rowsRemoved.connect(pane.update_count)
            pane.proxy.modelReset.connect(pane.update_count)
            pane.update_count()

        self.console = QPlainTextEdit(readOnly=True)
        self.console.setMaximumBlockCount(5000)
        self.console.setStyleSheet("font-family: monospace;")
        self.console_in = QLineEdit(placeholderText="Send raw command to board "
                                    "(b, w, x, s, j, t, ?) and press Enter")
        self.console_in.returnPressed.connect(self.send_console)
        console_w = QWidget()
        cl = QVBoxLayout(console_w)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.addWidget(self.console, 1)
        cl.addWidget(self.console_in)

        self.survey = SurveyTab(str(db_path), self.send_board)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.wifi, "WiFi")
        self.tabs.addTab(self.ble, "Bluetooth")
        self.tabs.addTab(self.survey, "Survey")
        self.tabs.addTab(console_w, "Console")
        self.tabs.currentChanged.connect(self.refresh_charts)
        self.setCentralWidget(self.tabs)

        self._build_toolbar(port, record)
        self._build_menu()
        self._build_statusbar()

        self.ticker = QTimer(self, interval=1000)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start()

        self.update_tab_titles()
        if self.port_box.currentText():
            QTimer.singleShot(0, self.connect_board)

    # ---- UI construction -------------------------------------------------

    def _build_toolbar(self, port, record):
        tb = QToolBar("Main", movable=False)
        self.addToolBar(tb)

        tb.addWidget(QLabel(" Port "))
        self.port_box = QComboBox(editable=True, minimumContentsLength=22)
        self.refresh_ports(prefer=port)
        tb.addWidget(self.port_box)
        refresh = QAction("↻", self, toolTip="Rescan serial ports")
        refresh.triggered.connect(lambda: self.refresh_ports())
        tb.addAction(refresh)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.clicked.connect(self.toggle_connection)
        tb.addWidget(self.connect_btn)
        tb.addSeparator()

        tb.addWidget(QLabel(" Scan "))
        self.mode_group = QActionGroup(self, exclusive=True)
        for key, text in (("wifi", "WiFi"), ("bt", "Bluetooth"), ("both", "Both")):
            a = QAction(text, self, checkable=True, checked=(key == self.mode))
            a.setData(key)
            a.setToolTip(f"Scan {MODE_LABELS[key]}")
            self.mode_group.addAction(a)
            tb.addAction(a)
        self.mode_group.triggered.connect(self.change_mode)

        self.pause_act = QAction("Pause", self, checkable=True, shortcut="Space",
                                 toolTip="Pause / resume scanning (Space)")
        self.pause_act.toggled.connect(self.toggle_pause)
        tb.addAction(self.pause_act)
        tb.addSeparator()

        clear = QAction("Clear", self, toolTip="Forget all devices seen so far")
        clear.triggered.connect(self.clear)
        tb.addAction(clear)

        self.record_box = QCheckBox("Log to database", checked=record)
        self.record_box.setToolTip(f"Save every scan to {self.db_path}\n"
                                   "(takes effect on next connect)")
        tb.addWidget(self.record_box)

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        for text, model in (("Export WiFi as CSV…", self.wifi_model),
                            ("Export Bluetooth as CSV…", self.ble_model)):
            a = m.addAction(text)
            a.triggered.connect(lambda _=False, mdl=model: self.export_csv(mdl))
        m.addSeparator()
        q = m.addAction("Quit")
        q.setShortcut(QKeySequence.Quit)
        q.triggered.connect(self.close)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.conn_label = QLabel("Not connected")
        self.activity = QLabel()
        self.heap_label = QLabel()
        self.session_label = QLabel()
        sb.addWidget(self.conn_label)
        sb.addWidget(self.activity, 1)
        sb.addPermanentWidget(self.heap_label)
        sb.addPermanentWidget(self.session_label)

    # ---- connection ------------------------------------------------------

    def refresh_ports(self, prefer=None):
        current = prefer or self.port_box.currentText()
        self.port_box.clear()
        ports = list_ports()
        self.port_box.addItems(ports)
        if current:
            if current not in ports:
                self.port_box.addItem(current)
            self.port_box.setCurrentText(current)

    def toggle_connection(self):
        if self.worker:
            self.disconnect_board()
        else:
            self.connect_board()

    def connect_board(self):
        port = self.port_box.currentText().strip()
        if not port or self.worker:
            self.connect_btn.setChecked(bool(self.worker))
            return
        mode = "idle" if self.paused else self.mode
        from .worker import SerialWorker
        self.worker = SerialWorker(port, mode, str(self.db_path), self.record_box.isChecked())
        self.worker.raw.connect(self.on_raw)
        self.worker.event.connect(self.on_event)
        self.worker.scan.connect(self.on_scan)
        self.worker.status.connect(self.on_status)
        self.worker.session.connect(self.on_session)
        self.worker.finished.connect(self.on_worker_finished)
        self.conn_label.setText(f"Connecting to {port}…")
        self.connect_btn.setChecked(True)
        self.connect_btn.setText("Disconnect")
        self.port_box.setEnabled(False)
        self.worker.start()

    def disconnect_board(self):
        if self.worker:
            self.worker.stop()

    def on_worker_finished(self):
        self.worker = None
        self.survey.set_connected(False)
        self.connect_btn.setChecked(False)
        self.connect_btn.setText("Connect")
        self.port_box.setEnabled(True)
        self.activity.setText("")

    def send_board(self, data):
        if not self.worker:
            return False
        self.worker.send_raw(data)
        return True

    def on_status(self, msg, connected):
        self.survey.set_connected(connected)
        self.conn_label.setText(("● " if connected else "○ ") + msg)
        self.log(f"[gui] {msg}")

    def on_session(self, name):
        self.session_label.setText(f"Logging session {name}" if name else "")

    # ---- board events ----------------------------------------------------

    def on_raw(self, line):
        self.log(line)

    def on_event(self, ev):
        kind = ev["ev"]
        if kind in ("button", "hello"):
            self.survey.on_event(ev)
        if kind == "button":
            self.log(f"[board] BOOT: {ev['action']} (mark {ev.get('mark')})")
        if kind == "scan_start":
            what = "WiFi" if ev["kind"] == "wifi" else "Bluetooth"
            self.activity.setText(f"Scanning {what}… (#{ev['scan']})")
        elif kind == "mode":
            if ev["mode"] == "idle":
                self.activity.setText("Paused")
            self.log(f"[board] mode → {MODE_LABELS.get(ev['mode'], ev['mode'])}")
        elif kind == "hello":
            self.log(f"[board] esp32-ai v{ev.get('ver')} mac {ev.get('mac')} "
                     f"mode {ev.get('mode')}")

    def on_scan(self, kind, index, rows, done):
        self.survey.on_scan(kind, index, rows, done)
        model = self.wifi_model if kind == "wifi" else self.ble_model
        model.apply_scan(index, rows)
        pane = self.wifi if kind == "wifi" else self.ble
        pane.proxy.invalidate()
        pane.update_count()
        self.update_tab_titles()
        if "heap" in done:
            self.heap_label.setText(f"ESP32 free heap {done['heap'] // 1024} KB")
        what = "WiFi" if kind == "wifi" else "Bluetooth"
        self.activity.setText(f"{what} scan #{index}: {len(rows)} found in "
                              f"{done.get('ms', 0) / 1000:.1f}s")
        self.refresh_charts()

    # ---- actions ---------------------------------------------------------

    def change_mode(self, action):
        self.mode = action.data()
        if self.worker and not self.paused:
            self.worker.set_mode(self.mode)

    def toggle_pause(self, paused):
        self.paused = paused
        self.pause_act.setText("Resume" if paused else "Pause")
        if self.worker:
            self.worker.set_mode("idle" if paused else self.mode)

    def clear(self):
        self.wifi_model.clear()
        self.ble_model.clear()
        self.update_tab_titles()
        self.refresh_charts()

    def send_console(self):
        text = self.console_in.text()
        self.console_in.clear()
        if text and self.worker:
            self.log(f"> {text}")
            self.worker.send_raw(text.encode())

    def export_csv(self, model):
        kind = "wifi" if model is self.wifi_model else "bluetooth"
        default = f"{kind}-{datetime.now():%Y%m%d-%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", default, "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c.title for c in model.cols] + ["Last seen (time)"])
            for r in model.rows:
                w.writerow([c.text(r) for c in model.cols]
                           + [datetime.fromtimestamp(r.last).isoformat(timespec="seconds")])
        self.statusBar().showMessage(f"Exported {len(model.rows)} rows to {path}", 5000)

    # ---- refresh ---------------------------------------------------------

    def refresh_charts(self, *_):
        if self.tabs.currentWidget() is self.wifi:
            sel = [r.key for r in self.wifi.selected()]
            self.channel_map.show_networks(self.wifi_model.current(), highlight=sel)
            self.wifi_history.show_records(self.wifi.chart_records())
        elif self.tabs.currentWidget() is self.ble:
            self.ble_history.show_records(self.ble.chart_records())
            sel = self.ble.selected()
            if sel:
                r = sel[0]
                self.ble_details.setPlainText(
                    ble_details(r.d)
                    + f"\n\nSignal {r.rssi} dBm (best {r.best}), seen in {r.seen} scans, "
                      f"first {datetime.fromtimestamp(r.first):%H:%M:%S}, "
                      f"last {datetime.fromtimestamp(r.last):%H:%M:%S}")
            else:
                self.ble_details.setPlainText("Select a device to see everything it advertises.")

    def update_tab_titles(self):
        self.tabs.setTabText(0, f"WiFi ({len(self.wifi_model.current())})")
        self.tabs.setTabText(1, f"Bluetooth ({len(self.ble_model.current())})")

    def tick(self):
        self.wifi_model.tick()
        self.ble_model.tick()

    def log(self, line):
        self.console.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line}")

    def closeEvent(self, e):
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        super().closeEvent(e)
