"""Survey tab: walk a route, capture at each stop with the BOOT button, and see
the heat map fill in on the floor plan."""

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSlider, QSplitter,
                               QVBoxLayout, QWidget)

from .. import PROJECT_ROOT
from .. import survey as sv
from .stop_devices import StopDevicesWindow

DONE = QColor("#3fb950")
SKIPPED = QColor("#6e7681")
CURRENT = QColor("#f0883e")
CAPTURING = QColor("#f85149")


class FloorPlan(QWidget):
    """The floor plan image with the heat overlay, stops and their values."""

    clicked = Signal(float, float, object)  # image x, y, stop circle hit (or None)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(500, 300)
        self.setMouseTracking(True)
        self.pixmap = None
        self.heat = None          # QImage, grid-sized; scaled over the plan
        self.stops = []           # [(name, x, y)]
        self.status = {}          # stop -> "done" / "skipped"
        self.values = {}          # stop -> float
        self.unit = ""
        self.current = None
        self.inspected = None     # stop whose devices are shown
        self.capturing = False
        self._pulse = 0
        self._timer = QTimer(self, interval=120)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_plan(self, path):
        self.pixmap = QPixmap(path) if path and Path(path).exists() else None
        self.update()

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

    def _paint(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.pixmap:
            p.setPen(self.palette().text().color())
            p.drawText(self.rect(), Qt.AlignCenter, "No floor plan loaded")
            return
        s, ox, oy = self._geometry()
        target = QRectF(ox, oy, self.pixmap.width() * s, self.pixmap.height() * s)
        p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        if self.heat is not None:
            p.drawImage(target, self.heat)

        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(7.0, 9 * s))
        p.setFont(font)
        r = max(9.0, 12 * s)
        for i, (name, x, y) in enumerate(self.stops):
            c = QPointF(ox + x * s, oy + y * s)
            st = self.status.get(i)
            if i == self.current:
                grow = (self._pulse if self._pulse < 8 else 16 - self._pulse) * 0.9
                color = CAPTURING if self.capturing else CURRENT
                p.setPen(QPen(color, 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r + 4 + grow, r + 4 + grow)
            if i == self.inspected:
                p.setPen(QPen(QColor("white"), 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r + 5, r + 5)
            fill = DONE if st == "done" else SKIPPED if st == "skipped" else QColor("#1f6feb")
            p.setPen(QPen(QColor("white"), 1.5))
            p.setBrush(fill)
            p.drawEllipse(c, r, r)
            p.setPen(QColor("white"))
            p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, str(i + 1))
            v = self.values.get(i)
            label = name if v is None else f"{name}  {v:.0f}{' dBm' if self.unit == 'dBm' else ''}"
            box = QRectF(c.x() - 90, c.y() + r + 2, 180, 18 * max(s, 0.8))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            w = p.fontMetrics().horizontalAdvance(label) + 10
            p.drawRoundedRect(QRectF(c.x() - w / 2, box.y(), w, box.height()), 4, 4)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignCenter, label)

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
    def __init__(self):
        super().__init__()
        self.setFixedHeight(38)
        self.stops, self.vmin, self.vmax, self.unit = sv.SIGNAL_STOPS, -90, -40, "dBm"

    def set_scale(self, stops, vmin, vmax, unit):
        self.stops, self.vmin, self.vmax, self.unit = stops, vmin, vmax, unit
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        try:
            bar = QRectF(4, 4, self.width() - 8, 14)
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            for i, c in enumerate(self.stops):
                g.setColorAt(i / (len(self.stops) - 1), QColor(c))
            p.fillRect(bar, g)
            p.setPen(self.palette().text().color())
            lo = f"{self.vmin:.0f}"
            hi = f"{self.vmax:.0f} {self.unit}"
            p.drawText(QRectF(4, 20, bar.width(), 16), Qt.AlignLeft, lo + (" dBm" if self.unit == "dBm" else ""))
            p.drawText(QRectF(4, 20, bar.width(), 16), Qt.AlignRight, hi)
        finally:
            p.end()


class SurveyTab(QWidget):
    """send(cmd_bytes) writes to the board; returns False when not connected."""

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
        self.devices_win = None

        # --- survey controls
        self.route_label = QLabel()
        load = QPushButton("Load route…")
        load.clicked.connect(self.choose_route)
        self.survey_box = QComboBox()
        self.survey_box.activated.connect(self.survey_chosen)
        top = QHBoxLayout()
        top.addWidget(self.route_label, 1)
        top.addWidget(load)

        self.status = QLabel(wordWrap=True, textFormat=Qt.RichText)
        self.status.setMinimumHeight(70)
        self.status.setStyleSheet("font-size: 13pt; padding: 6px; border-radius: 6px;"
                                  "background: #161b22;")
        self.stop_list = QListWidget()
        self.stop_list.currentRowChanged.connect(self._list_row)
        self.capture_btn = QPushButton("Start / stop capture")
        self.capture_btn.setToolTip("Same as a short press of BOOT on the board")
        self.capture_btn.clicked.connect(lambda: self._send(b"m"))
        self.skip_btn = QPushButton("Skip stop")
        self.skip_btn.setToolTip("Same as holding BOOT for 1 s")
        self.skip_btn.clicked.connect(lambda: self._send(b"k"))
        btns = QHBoxLayout()
        btns.addWidget(self.capture_btn)
        btns.addWidget(self.skip_btn)
        self.import_btn = QPushButton("Import from board")
        self.import_btn.setToolTip("Download the captures the board stored while running "
                                   "on its own (power bank) and turn them into a survey")
        self.import_btn.clicked.connect(self.import_from_board)

        sbox = QGroupBox("Survey")
        sl = QVBoxLayout(sbox)
        sl.addLayout(top)
        sl.addWidget(self.survey_box)
        sl.addWidget(self.status)
        sl.addWidget(QLabel("Stops (select one to capture it next):"))
        sl.addWidget(self.stop_list, 1)
        sl.addLayout(btns)
        sl.addWidget(self.import_btn)

        # --- heat map controls
        self.layer_box = QComboBox()
        for key, (label, *_rest) in sv.LAYERS.items():
            self.layer_box.addItem(label, key)
        self.layer_box.currentIndexChanged.connect(self._layer_changed)
        self.target_box = QComboBox()
        self.target_box.setMaxVisibleItems(25)
        self.target_box.currentIndexChanged.connect(self.refresh_heat)
        self.reach = QSlider(Qt.Horizontal, minimum=60, maximum=500, value=220)
        self.reach.setToolTip("How far from a stop the map is drawn (floor-plan pixels)")
        self.reach.valueChanged.connect(self.refresh_heat)
        self.legend = Legend()
        hbox = QGroupBox("Heat map")
        hl = QFormLayout(hbox)
        hl.addRow("Show", self.layer_box)
        hl.addRow("Target", self.target_box)
        hl.addRow("Reach", self.reach)
        hl.addRow(self.legend)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(sbox, 1)
        ll.addWidget(hbox)
        left.setMinimumWidth(330)
        left.setMaximumWidth(460)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.plan)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

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
        self.route_label.setText(f"Route: <b>{route['name']}</b> ({len(route['stops'])} stops)")
        self.plan.set_plan(route["floorplan"])
        self.plan.stops = [(s["name"], s["x"], s["y"]) for s in route["stops"]]
        self.current = self._next_open(0)
        self.plan.inspected = None
        if self.devices_win:
            self.devices_win.hide()
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
        self.survey_box.addItem("New survey (starts on the first capture)", None)
        for sid, name, done, total in self.store.surveys():
            self.survey_box.addItem(f"{name}  ·  {done}/{total} stops", sid)
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
        if self.devices_win is None:
            self.devices_win = StopDevicesWindow(self)
            self.devices_win.finished.connect(self._devices_closed)
        p = self.points[stop]
        loudest = sv.loudest_stops(self.points)
        devices = sv.stop_devices(p)
        for d in devices:
            where = loudest.get((d["kind"], d["identifier"]), (stop, 0))[0]
            d["loudest"] = where
            d["loudest_name"] = f"{where + 1}. {self.points[where].name}"
        self.devices_win.show_stop(stop + 1, p, devices)
        self.plan.inspected = stop
        self.plan.update()

    def _devices_closed(self):
        self.plan.inspected = None
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

    def _fill_stop_list(self):
        self.stop_list.blockSignals(True)
        self.stop_list.clear()
        if self.route:
            for i, s in enumerate(self.route["stops"]):
                p = self.points.get(i)
                if p and p.status == "done":
                    mark = f"✓  {p.count('wifi')} WiFi · {p.count('ble')} BT"
                elif p:
                    mark = "skipped"
                else:
                    mark = ""
                item = QListWidgetItem(f"{i + 1}. {s['name']}    {mark}")
                if p and p.status == "done":
                    item.setForeground(DONE)
                elif p:
                    item.setForeground(SKIPPED)
                self.stop_list.addItem(item)
            active = self.mark["stop"] if self.mark else self.current
            if active is not None:
                self.stop_list.setCurrentRow(active)
        self.stop_list.blockSignals(False)

    def update_status(self):
        if not self.route:
            html = "Load a route to start."
        elif self.mark:
            s = self.route["stops"][self.mark["stop"]]
            secs = int(time.time() - self.mark["started"])
            nw = sum(1 for _, sc in self.mark["scans"] if sc.kind == "wifi")
            nb = sum(1 for _, sc in self.mark["scans"] if sc.kind == "ble")
            html = (f"<span style='color:{CAPTURING.name()}'>●</span> Capturing "
                    f"<b>{s['name']}</b> · {secs} s<br>"
                    f"<small>{nw} WiFi + {nb} Bluetooth scans so far. "
                    f"Press BOOT to stop.</small>")
        elif self.current is None:
            html = ("✓ Survey complete. Click the map to see what was heard there; "
                    "pick a stop in the list to redo it.")
        else:
            s = self.route["stops"][self.current]
            html = (f"Next: <b>{s['name']}</b> ({self.current + 1}/{len(self.route['stops'])})"
                    f"<br><small>Go there and press BOOT to start. "
                    f"Hold BOOT 1 s to skip.</small>")
        if not getattr(self, "connected", False):
            html += "<br><small style='color:#d29922'>Board not connected.</small>"
        elif self.board_log and self.board_log[0]:
            stops, size = self.board_log
            html += (f"<br><small style='color:#58a6ff'>The board has {stops} stops stored "
                     f"from a walk ({size // 1024} KB): click Import from board.</small>")
        if self.message:
            html += f"<br><small>{self.message}</small>"
        self.status.setText(html)

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
        self.target_box.setEnabled(sv.LAYERS[layer][3])
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
        self.plan.unit = unit
        stops = sv.SIGNAL_STOPS if unit == "dBm" else sv.COUNT_STOPS
        vmin, vmax = sv.value_range(layer, list(values.values()))
        self.legend.set_scale(stops, vmin, vmax, unit)
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
        self.plan.update()
