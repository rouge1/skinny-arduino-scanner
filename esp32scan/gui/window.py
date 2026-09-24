"""Main window: a rail on the left (pages + the board's controls) and the
readings on the right."""

import csv
import time
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QSizePolicy,
                               QSplitter, QStackedWidget, QTableView, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import DEFAULT_DB
from ..decode import ble_details
from ..link import list_ports
from ..protocol import MODE_LABELS
from . import theme as T
from .models import BLE_COLS, WIFI_COLS, DeviceFilter, DeviceModel
from .survey_tab import SurveyTab
from .widgets import ChannelMap, SignalDelegate, SignalHistory, column_menu

TOP_N = 6  # devices charted when nothing is selected


def button(text, primary=False, flat=False, tip=None):
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if flat:
        b.setFlat(True)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text="", name=None, **kw):
    w = QLabel(text, **kw)
    if name:
        w.setObjectName(name)
    return w


def restyle(w):
    """Re-apply the stylesheet after changing a property it matches on."""
    w.style().unpolish(w)
    w.style().polish(w)


class NavButton(QAbstractButton):
    """A rail entry: page name on the left, a count on the right, an accent
    bar when it's the current page."""

    def __init__(self, text):
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.count = ""
        self.key_focus = False
        self.setFixedHeight(34)
        self.setFocusPolicy(Qt.TabFocus)

    def set_count(self, text):
        if text != self.count:
            self.count = text
            self.update()

    def sizeHint(self):
        return QSize(200, 34)

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect())
        if self.isChecked() or self.underMouse():
            p.fillRect(r, QColor(T.RAISED if self.isChecked() else T.GROUND))
        if self.isChecked():
            p.fillRect(QRectF(0, 6, 3, r.height() - 12), QColor(T.ACCENT))
        if self.hasFocus() and self.key_focus:
            p.setPen(QColor(T.ACCENT))
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        f = T.ui_font(11.5, QFont.Weight.Medium if self.isChecked() else QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(T.INK if self.isChecked() else T.MUTED))
        p.drawText(r.adjusted(16, 0, -12, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.setFont(T.tabular(T.ui_font(10.5)))
        p.setPen(QColor(T.MUTED))
        p.drawText(r.adjusted(16, 0, -14, 0), Qt.AlignVCenter | Qt.AlignRight, self.count)
        p.end()

    def focusInEvent(self, e):
        self.key_focus = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class Segmented(QFrame):
    """Exclusive options stacked in one outlined box; the chosen one is raised
    with an accent edge. Emits the chosen key."""

    chosen = Signal(str)

    def __init__(self, options, current):
        super().__init__(objectName="segments")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.group = QButtonGroup(self, exclusive=True)
        for key, text, tip in options:
            b = QPushButton(text, checkable=True, checked=(key == current), toolTip=tip)
            b.setProperty("segment", True)
            b.setProperty("key", key)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b)
            lay.addWidget(b)
        self.group.buttonClicked.connect(lambda b: self.chosen.emit(b.property("key")))


class DevicePane(QWidget):
    """Search bar + sortable table + charts for one kind of device."""

    def __init__(self, model, charts, key, noun):
        super().__init__()
        self.model = model
        self.proxy = DeviceFilter(model)
        self.noun = noun

        self.filter = QLineEdit(placeholderText=f"Search {noun}")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumWidth(260)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.only_current = QCheckBox("Latest scan only")
        self.only_current.setToolTip("Hide anything the last scan didn't hear")
        self.only_current.toggled.connect(self.proxy.set_only_current)
        self.count = label(name="count")
        self.export_btn = button("Export CSV", flat=True)
        self.clear_btn = button("Forget all", flat=True,
                                tip="Forget every WiFi network and Bluetooth device seen so far")

        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 12, 8)
        bar.setSpacing(14)
        bar.addWidget(self.filter)
        bar.addWidget(self.only_current)
        bar.addStretch(1)
        bar.addWidget(self.count)
        bar.addWidget(self.export_btn)
        bar.addWidget(self.clear_btn)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        signal_col = next(i for i, c in enumerate(model.cols) if c.rssi)
        self.table.sortByColumn(signal_col, Qt.DescendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QTableView.ScrollPerPixel)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setHighlightSections(False)
        for i, c in enumerate(model.cols):
            self.table.setColumnWidth(i, c.width)
            if c.stretch:
                hdr.setSectionResizeMode(i, QHeaderView.Stretch)
            if c.rssi:
                self.table.setItemDelegateForColumn(i, SignalDelegate(self.table))
        column_menu(self.table, key, [c.title for c in model.cols if c.hidden])

        self.charts = QTabWidget()
        self.charts.setDocumentMode(True)
        for title, w in charts:
            self.charts.addTab(w, title)
        chart_box = QWidget()
        cl = QVBoxLayout(chart_box)
        cl.setContentsMargins(16, 6, 12, 8)
        cl.addWidget(self.charts)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self.table)
        split.addWidget(chart_box)
        split.setSizes([480, 300])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
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
        if not total:
            text = "Nothing heard yet"
        elif shown != total:
            text = f"{shown} of {total} shown"
        else:
            text = f"{cur} in the latest scan, {total} in total"
        self.count.setText(text)


class MainWindow(QMainWindow):
    def __init__(self, port=None, mode="both", db_path=DEFAULT_DB, record=True):
        super().__init__()
        self.setWindowTitle("ESP32 WiFi + Bluetooth Scanner")
        self.resize(1360, 860)
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
                                ("Signal history", self.wifi_history)], "wifi", "networks")
        self.ble_details = QPlainTextEdit(readOnly=True)
        self.ble_details.setFont(T.mono_font(9))
        self.ble_details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.ble = DevicePane(self.ble_model, [("Signal history", self.ble_history),
                                                  ("Details", self.ble_details)],
                              "ble", "devices")
        for pane in (self.wifi, self.ble):
            pane.table.selectionModel().selectionChanged.connect(self.refresh_charts)
            pane.proxy.rowsInserted.connect(pane.update_count)
            pane.proxy.rowsRemoved.connect(pane.update_count)
            pane.proxy.modelReset.connect(pane.update_count)
            pane.proxy.layoutChanged.connect(pane.update_count)
            pane.clear_btn.clicked.connect(self.clear)
            pane.update_count()
        self.wifi.export_btn.clicked.connect(lambda: self.export_csv(self.wifi_model))
        self.ble.export_btn.clicked.connect(lambda: self.export_csv(self.ble_model))

        self.console = QPlainTextEdit(readOnly=True)
        self.console.setMaximumBlockCount(5000)
        self.console.setFont(T.mono_font(9))
        self.console.setFrameShape(QFrame.NoFrame)
        self.console_in = QLineEdit(placeholderText="Send a command to the board "
                                    "(b, w, x, s, j, t, ?) and press Enter")
        self.console_in.setFont(T.mono_font(9))
        self.console_in.returnPressed.connect(self.send_console)
        console_w = QWidget()
        cl = QVBoxLayout(console_w)
        cl.setContentsMargins(16, 12, 12, 12)
        cl.setSpacing(8)
        cl.addWidget(self.console, 1)
        cl.addWidget(self.console_in)

        self.survey = SurveyTab(str(db_path), self.send_board)
        self.survey.changed.connect(self.update_nav)

        self.pages = QStackedWidget()
        self.nav = QButtonGroup(self, exclusive=True)
        self.nav_buttons = []
        for i, (title, page) in enumerate((("WiFi", self.wifi), ("Bluetooth", self.ble),
                                           ("Survey", self.survey), ("Console", console_w))):
            self.pages.addWidget(page)
            b = NavButton(title)
            b.setToolTip(f"{title} (Ctrl+{i + 1})")
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            a = QAction(self, shortcut=f"Ctrl+{i + 1}")
            a.triggered.connect(lambda _=False, b=b: b.click())
            self.addAction(a)
        start = 1 if mode == "fast" else 0  # Fast BLE opens on the Bluetooth list
        self.nav_buttons[start].setChecked(True)
        self.pages.setCurrentIndex(start)
        self.nav.idClicked.connect(self.show_page)

        central = QWidget()
        cl = QHBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._build_rail(port, record))
        cl.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self._build_shortcuts()

        self.ticker = QTimer(self, interval=1000)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start()

        self.update_nav()
        self.set_board_state(False, "Not connected")
        if self.port_box.currentText():
            QTimer.singleShot(0, self.connect_board)

    # ---- UI construction -------------------------------------------------

    def _build_rail(self, port, record):
        rail = QFrame(objectName="rail")
        rail.setFixedWidth(248)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(2)

        title = label("ESP32 Scanner", "railTitle")
        title.setContentsMargins(12, 0, 12, 12)
        lay.addWidget(title)
        for b in self.nav_buttons:
            lay.addWidget(b)
        lay.addStretch(1)

        board = QVBoxLayout()
        board.setContentsMargins(16, 0, 16, 0)
        board.setSpacing(8)
        rule = QFrame(objectName="railRule")
        board.addWidget(rule)
        board.addSpacing(6)
        board.addWidget(label("Board", "railSection"))

        self.conn_dot = QFrame(objectName="dot")
        self.conn_dot.setFixedSize(10, 10)
        self.conn_label = label()
        self.conn_label.setWordWrap(True)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.conn_dot, 0, Qt.AlignTop)
        row.addWidget(self.conn_label, 1)
        board.addLayout(row)

        self.port_row = QWidget()
        pr = QHBoxLayout(self.port_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(6)
        self.port_box = QComboBox(editable=True)
        self.port_box.setToolTip("Serial port")
        self.refresh_ports(prefer=port)
        refresh = QToolButton(text="↻", toolTip="Look for serial ports again")
        refresh.setProperty("flat", True)
        refresh.clicked.connect(lambda: self.refresh_ports())
        pr.addWidget(self.port_box, 1)
        pr.addWidget(refresh)
        board.addWidget(self.port_row)

        self.connect_btn = button("Connect", primary=True)
        self.connect_btn.clicked.connect(self.toggle_connection)
        board.addWidget(self.connect_btn)

        board.addSpacing(8)
        board.addWidget(label("Scan for", "railSection"))
        self.mode_seg = Segmented(
            [("wifi", "WiFi", "Scan WiFi"), ("bt", "Bluetooth", "Scan Bluetooth"),
             ("both", "Both", "Scan WiFi + Bluetooth"),
             ("fast", "Fast BLE", "Bluetooth only, 1 s scans: quick updates for tracking "
                                  "down one device")], self.mode)
        self.mode_seg.chosen.connect(self.change_mode)
        board.addWidget(self.mode_seg)
        self.pause_btn = button("Pause scanning", tip="Pause or resume scanning (Space)")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self.toggle_pause)
        board.addWidget(self.pause_btn)

        self.activity = label(name="railNote")
        self.heap_label = label(name="railNote")
        self.heap_label.setToolTip("Free memory on the ESP32 after the last scan")
        board.addWidget(self.activity)
        board.addWidget(self.heap_label)

        board.addSpacing(8)
        self.record_box = QCheckBox("Save scans to the database", checked=record)
        self.record_box.setToolTip(f"Save every scan to {self.db_path}\n"
                                   "(takes effect the next time you connect)")
        board.addWidget(self.record_box)
        self.session_label = label(name="railNote")
        self.session_label.setWordWrap(True)
        board.addWidget(self.session_label)
        lay.addLayout(board)
        return rail

    def _build_shortcuts(self):
        for keys, fn in ((QKeySequence.Quit, self.close),
                         ("Space", lambda: self.pause_btn.toggle()),
                         (QKeySequence.Find, self.focus_search)):
            a = QAction(self, shortcut=keys)
            a.triggered.connect(fn)
            self.addAction(a)

    def show_page(self, i):
        self.pages.setCurrentIndex(i)
        self.refresh_charts()

    def focus_search(self):
        page = self.pages.currentWidget()
        if isinstance(page, DevicePane):
            page.filter.setFocus()
            page.filter.selectAll()

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

    def set_board_state(self, connected, text, busy=False):
        self.conn_dot.setProperty("state", "on" if connected else "busy" if busy else "off")
        restyle(self.conn_dot)
        self.conn_dot.setContentsMargins(0, 0, 0, 0)
        self.conn_label.setText(text)
        self.port_row.setVisible(not (connected or busy))
        self.connect_btn.setText("Disconnect" if (connected or busy) else "Connect")
        self.connect_btn.setProperty("primary", not (connected or busy))
        restyle(self.connect_btn)
        self.mode_seg.setEnabled(True)

    def toggle_connection(self):
        if self.worker:
            self.disconnect_board()
        else:
            self.connect_board()

    def connect_board(self):
        port = self.port_box.currentText().strip()
        if not port or self.worker:
            return
        mode = "idle" if self.paused else self.mode
        from .worker import SerialWorker
        self.worker = SerialWorker(port, mode, str(self.db_path), self.record_box.isChecked())
        self.worker.raw.connect(self.on_raw)
        self.worker.event.connect(self.on_event)
        self.worker.scan.connect(self.on_scan)
        self.worker.status.connect(self.on_status)
        self.worker.session.connect(self.on_session)
        self.worker.log.connect(self.survey.on_log)
        self.worker.finished.connect(self.on_worker_finished)
        self.set_board_state(False, f"Connecting to {port}", busy=True)
        self.worker.start()

    def disconnect_board(self):
        if self.worker:
            self.worker.stop()

    def on_worker_finished(self):
        self.worker = None
        self.survey.set_connected(False)
        if self.conn_label.text().startswith(("Connected", "Connecting")):
            self.set_board_state(False, "Not connected")
        else:
            self.set_board_state(False, self.conn_label.text())
        self.activity.setText("")

    def send_board(self, data):
        if not self.worker:
            return False
        self.worker.send_raw(data)
        return True

    def on_status(self, msg, connected):
        self.survey.set_connected(connected)
        port = self.port_box.currentText()
        if connected:
            self.set_board_state(True, f"Connected<br><span style='color:{T.MUTED}'>{port}"
                                       "</span>")
        else:
            self.set_board_state(False, msg)
        self.log(f"[gui] {msg}")

    def on_session(self, name):
        self.session_label.setText(f"Session {name}" if name else "")

    # ---- board events ----------------------------------------------------

    def on_raw(self, line):
        self.log(line)

    def on_event(self, ev):
        kind = ev["ev"]
        if kind in ("button", "hello", "log_cleared"):
            self.survey.on_event(ev)
        if kind == "button":
            self.log(f"[board] BOOT: {ev['action']} (mark {ev.get('mark')})")
        if kind == "scan_start":
            what = "WiFi" if ev["kind"] == "wifi" else "Bluetooth"
            self.activity.setText(f"Scanning {what}, scan {ev['scan']}")
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
        self.update_nav()
        if "heap" in done:
            self.heap_label.setText(f"{done['heap'] // 1024} KB free on the board")
        what = "WiFi" if kind == "wifi" else "Bluetooth"
        self.activity.setText(f"{what} scan {index} heard {len(rows)} in "
                              f"{done.get('ms', 0) / 1000:.1f} s")
        self.refresh_charts()

    # ---- actions ---------------------------------------------------------

    def change_mode(self, mode):
        self.mode = mode
        if self.worker and not self.paused:
            self.worker.set_mode(self.mode)
        if mode == "fast":  # Fast BLE is for hunting a device: show the Bluetooth list
            self.nav_buttons[1].click()

    def toggle_pause(self, paused):
        self.paused = paused
        self.pause_btn.setText("Resume scanning" if paused else "Pause scanning")
        if self.worker:
            self.worker.set_mode("idle" if paused else self.mode)

    def clear(self):
        self.wifi_model.clear()
        self.ble_model.clear()
        self.update_nav()
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
        pane = self.wifi if model is self.wifi_model else self.ble
        pane.count.setText(f"Exported {len(model.rows)} rows")
        QTimer.singleShot(4000, pane.update_count)

    # ---- refresh ---------------------------------------------------------

    def refresh_charts(self, *_):
        if self.pages.currentWidget() is self.wifi:
            sel = [r.key for r in self.wifi.selected()]
            self.channel_map.show_networks(self.wifi_model.current(), highlight=sel)
            self.wifi_history.show_records(self.wifi.chart_records())
        elif self.pages.currentWidget() is self.ble:
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

    def update_nav(self):
        w, b = self.wifi_model, self.ble_model
        self.nav_buttons[0].set_count(str(len(w.current())) if w.rows else "")
        self.nav_buttons[1].set_count(str(len(b.current())) if b.rows else "")
        self.nav_buttons[2].set_count(self.survey.progress())

    def tick(self):
        self.wifi_model.tick()
        self.ble_model.tick()
        self.update_nav()

    def log(self, line):
        self.console.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line}")

    def closeEvent(self, e):
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        super().closeEvent(e)
