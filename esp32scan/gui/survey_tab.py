"""Survey tab: walk a route, capture at each stop with the BOOT button, and see
the heat map fill in on the floor plan."""

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSlider, QSplitter, QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from .. import PROJECT_ROOT
from .. import survey as sv
from . import theme as T
from .stop_devices import StopDevicesPanel

STATE_ROLE = Qt.UserRole        # stop list: "done" / "skipped" / "open" / "next" / "capturing"
DETAIL_ROLE = Qt.UserRole + 1   # stop list: right-hand text
INSPECTED_ROLE = Qt.UserRole + 2  # stop list: the stop whose devices are listed

# What a layer's number counts, short (map labels) and long (colour scale).
UNITS = {"wifi_count": ("APs", "access points"), "ble_count": ("BT", "BT devices")}


def layer_units(layer):
    return UNITS.get(layer, ("dBm", "dBm"))


def ramp_at(stops, t):
    """Colour at t (0..1) along a list of hex stops."""
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    a, b = QColor(stops[i]), QColor(stops[i + 1])
    f = t - i
    return QColor.fromRgbF(*(a.getRgbF()[k] + (b.getRgbF()[k] - a.getRgbF()[k]) * f
                             for k in range(3)))


def paint_badge(p, c, r, number, state, font):
    """A stop's numbered disc, shared by the map and the stop list."""
    fill, edge, text, dash = {
        "done": (QColor(T.INK), QColor(T.INK), QColor(T.GROUND), False),
        "capturing": (QColor(T.ACCENT), QColor(T.ACCENT), QColor(T.GROUND), False),
        "skipped": (QColor(T.GROUND), QColor(T.FAINT), QColor(T.FAINT), True),
    }.get(state, (QColor(T.GROUND), QColor(T.INK), QColor(T.INK), False))
    if state in ("open", "next", "skipped"):
        fill.setAlpha(225)
    pen = QPen(QColor(T.ACCENT) if state == "next" else edge, 1.6)
    if dash:
        pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.setBrush(fill)
    p.drawEllipse(c, r, r)
    p.setFont(font)
    p.setPen(text)
    p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, str(number))


class FloorPlan(QWidget):
    """The floor plan image with the heat overlay, stops and their values."""

    clicked = Signal(float, float, object)  # image x, y, stop circle hit (or None)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(300, 200)
        self.setMouseTracking(True)
        self.pixmap = None
        self.heat = None          # QImage, grid-sized; scaled over the plan
        self.stops = []           # [(name, x, y)]
        self.status = {}          # stop -> "done" / "skipped"
        self.values = {}          # stop -> float
        self.unit = ""
        self.current = None
        self.inspected = None     # stop whose devices are shown
        self.legend = None        # colour scale, kept in the image's bottom-right corner
        self.capturing = False
        self._pulse = 0
        self._timer = QTimer(self, interval=120)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_plan(self, path):
        self.pixmap = QPixmap(path) if path and Path(path).exists() else None
        self._place_legend()
        self.update()

    def resizeEvent(self, e):
        self._place_legend()
        super().resizeEvent(e)

    def _place_legend(self):
        if not self.legend:
            return
        self.legend.setVisible(self.pixmap is not None and self.heat is not None)
        if self.pixmap:
            s, ox, oy = self._geometry()
            right = ox + self.pixmap.width() * s
            bottom = oy + self.pixmap.height() * s
            self.legend.move(int(right - self.legend.width() - 12),
                             int(bottom + 10) if bottom + 10 + self.legend.height()
                             <= self.height() else int(bottom - self.legend.height() - 12))

    def _tick(self):
        self._pulse = (self._pulse + 1) % 16
        if self.current is not None:
            self.update()

    def _geometry(self):
        """Scale and offset that fit the image into the widget."""
        if not self.pixmap:
            return 1.0, 0.0, 0.0
        s = min(self.width() / self.pixmap.width(), self.height() / self.pixmap.height())
        ox = (self.width() - self.pixmap.width() * s) / 2
        oy = (self.height() - self.pixmap.height() * s) / 2
        return s, ox, oy

    def paintEvent(self, _):
        p = QPainter(self)
        try:
            self._paint(p)
        except Exception as e:  # a paint exception must not take the app down
            p.setPen(QColor("red"))
            p.drawText(10, 20, f"paint error: {e}")
        finally:
            p.end()

    def _state(self, i):
        if i == self.current:
            return "capturing" if self.capturing else "next"
        return {"done": "done", "skipped": "skipped"}.get(self.status.get(i), "open")

    def _paint(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.GROUND))
        if not self.pixmap:
            p.setPen(QColor(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Load a route to see its floor plan")
            return
        s, ox, oy = self._geometry()
        target = QRectF(ox, oy, self.pixmap.width() * s, self.pixmap.height() * s)
        p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        if self.heat is not None:
            p.drawImage(target, self.heat)

        r = max(10.0, 12 * s)
        badge_font = T.ui_font(max(8.5, 10 * s), QFont.Weight.DemiBold)
        name_font = T.ui_font(max(8.5, 10 * s))
        value_font = T.tabular(T.ui_font(max(9.5, 11 * s), QFont.Weight.DemiBold))
        for i, (name, x, y) in enumerate(self.stops):
            c = QPointF(ox + x * s, oy + y * s)
            state = self._state(i)
            if i == self.current:
                grow = (self._pulse if self._pulse < 8 else 16 - self._pulse) * 0.7
                ring = QColor(T.ACCENT)
                ring.setAlphaF(0.9 - grow / 8)
                p.setPen(QPen(ring, 2.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r + 4 + grow, r + 4 + grow)
            if i == self.inspected:
                p.setPen(QPen(QColor(T.ACCENT), 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r + 4, r + 4)
            paint_badge(p, c, r, i + 1, state, badge_font)
            self._paint_label(p, c, r, name, self.values.get(i), name_font, value_font)

    def _paint_label(self, p, c, r, name, value, name_font, value_font):
        """A dark pill under the stop: the room name, then the layer's value."""
        p.setFont(name_font)
        nw = p.fontMetrics().horizontalAdvance(name)
        h = p.fontMetrics().height() + 4
        vtext = "" if value is None else f"{value:.0f}".replace("-", "−")
        utext = f" {self.unit}" if vtext and self.unit else ""
        uw = p.fontMetrics().horizontalAdvance(utext) if utext else 0
        p.setFont(value_font)
        vw = p.fontMetrics().horizontalAdvance(vtext) + 6 if vtext else 0
        w = nw + vw + uw + 12
        box = QRectF(c.x() - w / 2, c.y() + r + 4, w, h)
        bg = QColor(T.GROUND)
        bg.setAlpha(215)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(box, 3, 3)
        p.setFont(name_font)
        p.setPen(QColor(T.MUTED if vtext else T.INK))
        p.drawText(box.adjusted(6, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, name)
        if vtext:
            p.setFont(value_font)
            p.setPen(QColor(T.INK))
            p.drawText(box.adjusted(0, 0, -6 - uw, 0), Qt.AlignVCenter | Qt.AlignRight, vtext)
            p.setFont(name_font)
            p.setPen(QColor(T.MUTED))
            p.drawText(box.adjusted(0, 0, -6, 0), Qt.AlignVCenter | Qt.AlignRight, utext)

    def _stop_at(self, pos):
        s, ox, oy = self._geometry()
        for i, (_, x, y) in enumerate(self.stops):
            if (pos.x() - (ox + x * s)) ** 2 + (pos.y() - (oy + y * s)) ** 2 <= (16 * max(s, .7)) ** 2:
                return i
        return None

    def mousePressEvent(self, e):
        if not self.pixmap:
            return
        s, ox, oy = self._geometry()
        pos = e.position()
        self.clicked.emit((pos.x() - ox) / s, (pos.y() - oy) / s, self._stop_at(pos))

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self.pixmap else Qt.ArrowCursor)


class Legend(QWidget):
    """The colour scale as a segmented bar, like the tables' signal meter."""

    SEGMENTS = 10

    def __init__(self):
        super().__init__()
        self.setFixedSize(270, 34)
        self.stops, self.vmin, self.vmax, self.unit = sv.SIGNAL_STOPS, -90, -40, "dBm"

    def set_scale(self, stops, vmin, vmax, unit):
        self.stops, self.vmin, self.vmax, self.unit = stops, vmin, vmax, unit
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            bg = QColor(T.GROUND)
            bg.setAlpha(225)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()), 4, 4)
            p.translate(10, 4)
            h = self.height() - 8
            w = self.width() - 20
            p.setFont(T.tabular(T.ui_font(9.5)))
            fm = p.fontMetrics()
            lo = f"{self.vmin:.0f}".replace("-", "−")
            hi = f"{self.vmax:.0f}".replace("-", "−") + (f" {self.unit}" if self.unit else "")
            lw, hw = fm.horizontalAdvance(lo) + 8, fm.horizontalAdvance(hi) + 8
            bar = QRectF(lw, h / 2 - 5, w - lw - hw, 10)
            gap = 2
            seg = (bar.width() - gap * (self.SEGMENTS - 1)) / self.SEGMENTS
            p.setPen(Qt.NoPen)
            for k in range(self.SEGMENTS):
                p.setBrush(ramp_at(self.stops, (k + 0.5) / self.SEGMENTS))
                p.drawRect(QRectF(bar.x() + k * (seg + gap), bar.y(), seg, bar.height()))
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(0, 0, lw, h), Qt.AlignVCenter | Qt.AlignLeft, lo)
            p.drawText(QRectF(w - hw, 0, hw, h), Qt.AlignVCenter | Qt.AlignRight, hi)
        finally:
            p.end()


class StopDelegate(QStyledItemDelegate):
    """Stop list row: numbered badge, room name, and scans or 'Skipped' on the right."""

    def sizeHint(self, option, index):
        return QSize(200, 34)

    def paint(self, p, option, index):
        state = index.data(STATE_ROLE)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        inspected = bool(index.data(INSPECTED_ROLE))
        if inspected:
            tint = QColor(T.ACCENT)
            tint.setAlphaF(0.14)
            p.fillRect(rect, tint)
            p.fillRect(QRectF(rect.x(), rect.y() + 4, 3, rect.height() - 8), QColor(T.ACCENT))
        elif selected or hover:
            p.fillRect(rect, QColor(T.RAISED if selected else T.PANEL))
        c = QPointF(rect.x() + 20, rect.center().y())
        if inspected:
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 13.5, 13.5)
        paint_badge(p, c, 10, index.row() + 1, state, T.ui_font(9, QFont.Weight.DemiBold))
        p.setFont(T.ui_font(10.5, QFont.Weight.Medium if inspected or state in
                            ("next", "capturing") else QFont.Weight.Normal))
        p.setPen(QColor(T.FAINT if state == "skipped" else T.INK))
        text_rect = rect.adjusted(40, 0, -10, 0)
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, index.data(Qt.DisplayRole))
        p.setFont(T.tabular(T.ui_font(9.5)))
        p.setPen(QColor(T.ACCENT if state in ("next", "capturing") else T.MUTED))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignRight, index.data(DETAIL_ROLE) or "")
        p.restore()


class SurveyTab(QWidget):
    """send(cmd_bytes) writes to the board; returns False when not connected."""

    changed = Signal()  # stops captured / survey switched (the rail shows progress)

    def __init__(self, db_path, send):
        super().__init__()
        self.store = sv.SurveyStore(db_path)
        self.send = send
        self.route = None
        self.survey_id = None
        self.points = {}
        self.current = 0
        self.mark = None      # {"stop", "t0" (board ms), "wall", "scans": [(done, Scan)]}
        self.message = ""
        self.board_log = None  # (stops, bytes) stored on the board, from its hello

        self.plan = FloorPlan()
        self.plan.setToolTip("Click anywhere to list the devices heard at the nearest "
                             "captured stop")
        self.plan.clicked.connect(self.plan_clicked)
        self.devices = StopDevicesPanel()

        # --- left column: route, survey, status, stops, capture buttons
        self.route_label = QLabel(objectName="heading")
        load = QPushButton("Change route", flat=True)
        load.setToolTip("Load a different route (.json)")
        load.clicked.connect(self.choose_route)
        top = QHBoxLayout()
        top.addWidget(self.route_label, 1)
        top.addWidget(load)
        self.survey_box = QComboBox()
        self.survey_box.setToolTip("Past surveys of this route, newest first")
        self.survey_box.activated.connect(self.survey_chosen)

        self.card = QFrame(objectName="card")
        self.card_title = QLabel(objectName="cardTitle", wordWrap=True)
        self.card_body = QLabel(objectName="cardBody", wordWrap=True, textFormat=Qt.RichText)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(14, 10, 14, 12)
        cl.setSpacing(4)
        cl.addWidget(self.card_title)
        cl.addWidget(self.card_body)

        self.stop_list = QListWidget()
        self.stop_list.setItemDelegate(StopDelegate(self.stop_list))
        self.stop_list.setMouseTracking(True)
        self.stop_list.setFrameShape(QFrame.NoFrame)
        self.stop_list.setToolTip("Pick a stop to capture it next")
        self.stop_list.currentRowChanged.connect(self._list_row)

        self.capture_btn = QPushButton("Start capture")
        self.capture_btn.setProperty("primary", True)
        self.capture_btn.setToolTip("Same as a short press of BOOT on the board")
        self.capture_btn.clicked.connect(lambda: self._send(b"m"))
        self.skip_btn = QPushButton("Skip stop")
        self.skip_btn.setToolTip("Same as holding BOOT for 1 s")
        self.skip_btn.clicked.connect(lambda: self._send(b"k"))
        self.import_btn = QPushButton("Import from board")
        self.import_btn.setToolTip("Download the captures the board stored while running "
                                   "on its own (power bank) and turn them into a survey")
        self.import_btn.clicked.connect(self.import_from_board)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addWidget(self.skip_btn)
        btns.addWidget(self.import_btn)

        left = QFrame(objectName="side")
        left.setFixedWidth(300)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 14, 16, 16)
        ll.setSpacing(10)
        ll.addLayout(top)
        ll.addWidget(self.survey_box)
        ll.addSpacing(2)
        ll.addWidget(self.card)
        ll.addWidget(self.stop_list, 1)
        ll.addWidget(self.capture_btn)
        ll.addLayout(btns)

        # --- map bar: what the heat map shows, and its scale
        self.layer_box = QComboBox()
        for key, (label, *_rest) in sv.LAYERS.items():
            self.layer_box.addItem(label, key)
        self.layer_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.layer_box.setMinimumContentsLength(18)
        self.layer_box.currentIndexChanged.connect(self._layer_changed)
        self.target_box = QComboBox()
        self.target_box.setMaxVisibleItems(25)
        self.target_box.setMinimumContentsLength(18)
        self.target_box.setPlaceholderText("Nothing captured yet")
        self.target_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target_box.currentIndexChanged.connect(self.refresh_heat)
        self.reach = QSlider(Qt.Horizontal, minimum=60, maximum=500, value=220)
        self.reach.setMinimumWidth(60)
        self.reach.setMaximumWidth(110)
        self.reach.setToolTip("How far from a stop the colour spreads")
        self.reach.valueChanged.connect(self.refresh_heat)
        self.legend = Legend()
        self.legend.setParent(self.plan)
        self.plan.legend = self.legend
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 10)
        bar.setSpacing(10)
        bar.addWidget(QLabel("Show", objectName="fieldLabel"))
        bar.addWidget(self.layer_box, 2)
        bar.addWidget(self.target_box, 3)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Reach", objectName="fieldLabel"))
        bar.addWidget(self.reach, 1)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addLayout(bar)
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self.plan)
        split.addWidget(self.devices)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([520, 340])
        rl.addWidget(split, 1)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(left)
        lay.addWidget(right, 1)

        self._clock = QTimer(self, interval=1000)
        self._clock.timeout.connect(self.update_status)
        self._clock.start()

        route = sv.default_route()
        if route:
            self.set_route(route)
        self.set_connected(False)

    # ---- route / survey selection ----------------------------------------

    def set_route(self, route, survey_id=None, points=None):
        self.route = route
        self.survey_id = survey_id
        self.points = points or {}
        self.mark = None
        self.route_label.setText(route["name"])
        self.route_label.setToolTip(f"{len(route['stops'])} stops")
        self.plan.set_plan(route["floorplan"])
        self.plan.stops = [(s["name"], s["x"], s["y"]) for s in route["stops"]]
        self.current = self._next_open(0)
        self.plan.inspected = None
        self.devices.clear()
        self._mark_inspected()
        self._fill_surveys()
        self.refresh_all()

    def choose_route(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load route", str(PROJECT_ROOT / "routes"),
                                              "Route (*.json)")
        if path:
            self.set_route(sv.load_route(path))

    def _fill_surveys(self):
        self.survey_box.blockSignals(True)
        self.survey_box.clear()
        self.survey_box.addItem("New survey (starts at the first capture)", None)
        prefix = (self.route or {}).get("name", "")
        for sid, name, done, total in self.store.surveys():
            short = name[len(prefix):].strip() if prefix and name.startswith(prefix) else name
            self.survey_box.addItem(f"{short}, {done} of {total} stops", sid)
            if sid == self.survey_id:
                self.survey_box.setCurrentIndex(self.survey_box.count() - 1)
        self.survey_box.blockSignals(False)

    def survey_chosen(self, index):
        sid = self.survey_box.itemData(index)
        if sid is None:
            if self.route:
                self.set_route(sv.load_route(self.route["path"]) if "path" in self.route
                               else self.route)
            return
        route = self.store.route(sid)
        route["path"] = self.route.get("path", "") if self.route else ""
        self.set_route(route, sid, self.store.points(sid))

    def _next_open(self, start):
        n = len(self.route["stops"]) if self.route else 0
        for i in list(range(start, n)) + list(range(0, start)):
            if i not in self.points:
                return i
        return None

    def select_stop(self, i):
        if self.mark:
            self.message = "Finish the capture in progress first."
        else:
            self.current = i
            self.message = ""
        self.refresh_all(heat=False)

    NEAREST_PX = 250  # how far from a captured stop a click still picks it

    def plan_clicked(self, x, y, hit):
        """A stop that hasn't been captured becomes the next to capture; any other
        click shows the devices heard at the nearest captured stop."""
        if hit is not None and (hit not in self.points or self.points[hit].status != "done"):
            self.select_stop(hit)
            return
        done = [p for p in self.points.values() if p.status == "done"]
        if not done:
            return
        p = min(done, key=lambda p: (p.x - x) ** 2 + (p.y - y) ** 2)
        if hit is None and (p.x - x) ** 2 + (p.y - y) ** 2 > self.NEAREST_PX ** 2:
            return
        self.show_devices(p.stop)

    def show_devices(self, stop):
        p = self.points[stop]
        loudest = sv.loudest_stops(self.points)
        devices = sv.stop_devices(p)
        for d in devices:
            where = loudest.get((d["kind"], d["identifier"]), (stop, 0))[0]
            d["loudest"] = where
            d["loudest_name"] = self.points[where].name
        self.devices.show_stop(stop + 1, p, devices)
        self.plan.inspected = stop
        self._mark_inspected()
        self.plan.update()

    def _list_row(self, row):
        if row >= 0 and row != self.current and not self.mark:
            self.select_stop(row)

    # ---- board events ----------------------------------------------------

    def set_connected(self, connected):
        self.capture_btn.setEnabled(connected)
        self.skip_btn.setEnabled(connected)
        self.import_btn.setEnabled(connected)
        self.connected = connected
        if not connected:
            self.board_log = None
        self.update_status()

    def _send(self, cmd):
        if self.send(cmd):
            return True
        self.message = "Not connected to the board."
        self.update_status()
        return False

    def on_event(self, ev):
        kind = ev.get("ev")
        if kind == "button":
            action = ev["action"]
            if action == "start":
                self._start(ev)
            elif action == "stop":
                self._stop(ev)
            elif action == "skip":
                self._skip(ev)
            elif action == "reject":
                self.mark = None
                self.message = ("Too short: the board needs a complete WiFi and Bluetooth "
                                "scan inside a capture. Capture this stop again (20-30 s).")
                self.refresh_all(heat=False)
            elif action == "full":
                self.message = "The board's survey log is full. Import it, then clear it."
                self.update_status()
        elif kind == "hello":
            if "stops" in ev:
                self.board_log = (ev["stops"], ev.get("log_bytes", 0))
            if self.mark and not ev.get("marking"):
                self.mark = None
                self.message = "The board restarted during a capture. Capture this stop again."
            self.refresh_all(heat=False)
        elif kind == "log_cleared":
            self.board_log = (0, 0)
            self.message = (self.message + "<br>" if self.message else "") + \
                "Cleared the board's survey log."
            self.update_status()

    def on_scan(self, kind, index, rows, done):
        if self.mark and done.get("t0", -1) >= self.mark["t0"]:
            scan = sv.Scan(kind, index, [sv.reading(kind, r) for r in rows])
            self.mark["scans"].append((done, scan))
            self.update_status()

    def import_from_board(self):
        if self._send(b"d"):
            self.message = "Downloading the board's survey log…"
            self.update_status()

    def on_log(self, lines, begin):
        """The board's stored log arrived: replay it into a new survey."""
        if not self.route:
            self.message = "Load a route before importing."
            self.update_status()
            return
        points, notes = sv.import_log(lines, self.route)
        if not points:
            self.message = "The board has no stored captures."
            self.update_status()
            return
        route = self.route
        sid = self.store.new_survey(route, " (from board)")
        for p in points.values():
            self.store.save_point(sid, p)
        self.set_route(route, sid, self.store.points(sid))
        done = sum(1 for p in points.values() if p.status == "done")
        skipped = len(points) - done
        self.message = (f"Imported {done} captured and {skipped} skipped stops "
                        f"({begin.get('bytes', 0) // 1024} KB).")
        if notes:
            self.message += "<br>" + "<br>".join(notes)
        self.update_status()
        answer = QMessageBox.question(
            self, "Clear the board?",
            f"Imported {len(points)} stops into “{self.survey_box.currentText()}”.\n\n"
            "Clear the board's survey log so the next walk starts from stop 1?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self._send(b"c")

    def _wall(self, ev):
        t = ev.get("_wall") or time.time()
        return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")

    def _start(self, ev):
        if not self.route:
            self.message = "Load a route first."
        elif self.current is None:
            self.message = "Every stop is done. Click a stop to redo it."
        else:
            self.mark = {"stop": self.current, "t0": ev["t"], "wall": self._wall(ev),
                         "started": time.time(), "scans": []}
            self.message = ""
        self.refresh_all(heat=False)

    def _stop(self, ev):
        if not self.mark:
            self.message = ("Got a stop press without a start (the app may have "
                            "connected mid-capture). Press BOOT again to start.")
            self.refresh_all(heat=False)
            return
        m, self.mark = self.mark, None
        scans = [scan for done, scan in m["scans"] if done.get("t1", 0) <= ev["t"]]
        if not scans:
            self.message = ("Too short: no scan finished during that capture. "
                            "Capture this stop again for at least 10 s.")
            self.refresh_all(heat=False)
            return
        s = self.route["stops"][m["stop"]]
        point = sv.Point(m["stop"], s["name"], s["x"], s["y"], "done",
                         m["wall"], self._wall(ev), scans)
        self._save(point)
        self.message = (f"Saved <b>{s['name']}</b>: {point.count('wifi')} WiFi + "
                        f"{point.count('ble')} Bluetooth scans.")
        self._advance(m["stop"])

    def _skip(self, ev):
        stop = self.mark["stop"] if self.mark else self.current
        self.mark = None
        if stop is None:
            return
        s = self.route["stops"][stop]
        self._save(sv.Point(stop, s["name"], s["x"], s["y"], "skipped",
                            self._wall(ev), self._wall(ev), []))
        self.message = f"Skipped <b>{s['name']}</b>."
        self._advance(stop)

    def _save(self, point):
        if self.survey_id is None:
            self.survey_id = self.store.new_survey(self.route)
        self.store.save_point(self.survey_id, point)
        self.points[point.stop] = point
        self._fill_surveys()

    def _advance(self, stop):
        self.current = self._next_open(stop + 1)
        self.refresh_all()

    # ---- display ---------------------------------------------------------

    def refresh_all(self, heat=True):
        self.plan.status = {i: p.status for i, p in self.points.items()}
        self.plan.current = self.mark["stop"] if self.mark else self.current
        self.plan.capturing = bool(self.mark)
        self._fill_stop_list()
        if heat:
            self._fill_targets()
            self.refresh_heat()
        self.update_status()
        self.plan.update()
        self.changed.emit()

    def _fill_stop_list(self):
        self.stop_list.blockSignals(True)
        self.stop_list.clear()
        if self.route:
            active = self.mark["stop"] if self.mark else self.current
            for i, s in enumerate(self.route["stops"]):
                p = self.points.get(i)
                if i == active:
                    state = "capturing" if self.mark else "next"
                    detail = "capturing" if self.mark else "next"
                elif p and p.status == "done":
                    state, detail = "done", f"{p.count('wifi')} WiFi, {p.count('ble')} BT"
                elif p:
                    state, detail = "skipped", "skipped"
                else:
                    state, detail = "open", ""
                item = QListWidgetItem(s["name"])
                item.setData(STATE_ROLE, state)
                item.setData(DETAIL_ROLE, detail)
                item.setData(INSPECTED_ROLE, i == self.plan.inspected)
                if p and p.status == "done":
                    item.setToolTip(f"{p.count('wifi')} WiFi and {p.count('ble')} Bluetooth "
                                    "scans. Pick it to capture it again.")
                self.stop_list.addItem(item)
            if active is not None:
                self.stop_list.setCurrentRow(active)
        self.stop_list.blockSignals(False)

    def _mark_inspected(self):
        """Highlight the clicked stop in the list too, and scroll it into view."""
        for i in range(self.stop_list.count()):
            item = self.stop_list.item(i)
            item.setData(INSPECTED_ROLE, i == self.plan.inspected)
            if i == self.plan.inspected:
                self.stop_list.scrollToItem(item)

    def progress(self):
        """'7/10' for the rail, or '' with no route."""
        if not self.route:
            return ""
        done = sum(1 for p in self.points.values() if p.status == "done")
        return f"{done}/{len(self.route['stops'])}"

    def update_status(self):
        state, lines = "idle", []
        if not self.route:
            title = "No route loaded"
            lines.append("Load a route to start.")
        elif self.mark:
            s = self.route["stops"][self.mark["stop"]]
            secs = int(time.time() - self.mark["started"])
            nw = sum(1 for _, sc in self.mark["scans"] if sc.kind == "wifi")
            nb = sum(1 for _, sc in self.mark["scans"] if sc.kind == "ble")
            state, title = "capturing", f"Capturing {s['name']}, {secs} s"
            lines.append(f"{nw} WiFi and {nb} Bluetooth scans so far. Press BOOT to stop.")
        elif self.current is None:
            title = "Survey complete"
            lines.append("Click the map to list what was heard near a stop. "
                         "Pick a stop to capture it again.")
        else:
            s = self.route["stops"][self.current]
            state, title = "next", f"Next: {s['name']}"
            lines.append(f"Stop {self.current + 1} of {len(self.route['stops'])}. Go there and "
                         "press BOOT to start, or hold it for 1 s to skip.")
        connected = getattr(self, "connected", False)
        if not connected and state in ("next", "capturing"):
            lines.append(f"<span style='color:{T.WARN}'>Connect the board to capture "
                         "from here, or walk the route on a power bank.</span>")
        elif connected and self.board_log and self.board_log[0]:
            stops, size = self.board_log
            lines.append(f"<span style='color:{T.ACCENT}'>The board holds {stops} stops "
                         f"from a walk ({size // 1024} KB). Import them from the board."
                         "</span>")
        if self.message:
            lines.append(f"<span style='color:{T.INK}'>{self.message}</span>")
        self.card_title.setText(title)
        self.card_body.setText("<br>".join(lines))
        if self.card.property("state") != state:
            self.card.setProperty("state", state)
            self.card.style().unpolish(self.card)
            self.card.style().polish(self.card)
        self.capture_btn.setText("Stop capture" if self.mark else "Start capture")

    def _layer_changed(self):
        self._fill_targets()
        self.refresh_heat()

    def _fill_targets(self):
        layer = self.layer_box.currentData()
        keep = self.target_box.currentData()
        self.target_box.blockSignals(True)
        self.target_box.clear()
        for key, label in sv.targets(self.points, layer):
            self.target_box.addItem(label, key)
        if keep is not None:
            i = self.target_box.findData(keep)
            if i >= 0:
                self.target_box.setCurrentIndex(i)
        # With a placeholder set, QComboBox no longer selects the first item itself.
        if self.target_box.currentIndex() < 0 and self.target_box.count():
            self.target_box.setCurrentIndex(0)
        self.target_box.setVisible(sv.LAYERS[layer][3])
        self.target_box.blockSignals(False)

    def refresh_heat(self):
        layer = self.layer_box.currentData()
        _, _, unit, needs = sv.LAYERS[layer]
        target = self.target_box.currentData() if needs else None
        values = {}
        if not needs or target is not None:
            for i, p in self.points.items():
                v = sv.point_value(p, layer, target)
                if v is not None:
                    values[i] = v
        self.plan.values = values
        short, long_ = layer_units(layer)
        self.plan.unit = short
        stops = sv.SIGNAL_STOPS if unit == "dBm" else sv.COUNT_STOPS
        vmin, vmax = sv.value_range(layer, list(values.values()))
        self.legend.set_scale(stops, vmin, vmax, long_)
        pm = self.plan.pixmap
        if not values or pm is None:
            self.plan.heat = None
        else:
            pts = [(self.points[i].x, self.points[i].y, v) for i, v in values.items()]
            xs, ys, vs = zip(*pts)
            z, alpha = sv.idw_grid(xs, ys, vs, pm.width(), pm.height(),
                                   reach=self.reach.value())
            rgba = sv.colorize(z, alpha, vmin, vmax, stops)
            h, w, _ = rgba.shape
            self._rgba = rgba.copy(order="C")  # QImage doesn't own the buffer
            self.plan.heat = QImage(self._rgba.data, w, h, 4 * w, QImage.Format_RGBA8888)
        self.plan._place_legend()
        self.plan.update()
