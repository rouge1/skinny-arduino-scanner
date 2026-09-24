"""Custom widgets: the signal meter (table cell + legend), the two charts and
the right-click column menu."""

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu, QStyle, QStyledItemDelegate

from . import theme as T
from .models import BEST_ROLE, RSSI_ROLE, STALE_ROLE

RSSI_FLOOR = T.RSSI_FLOOR
RSSI_CEIL = T.RSSI_CEIL
SEGMENTS = (T.RSSI_CEIL - T.RSSI_FLOOR) // T.RSSI_STEP
SEG_W, SEG_GAP, SEG_H = 4, 2, 10
METER_W = SEGMENTS * (SEG_W + SEG_GAP) - SEG_GAP


def dbm(v):
    """-55 -> '−55' (a real minus sign, it lines up with tabular digits)."""
    return f"{v:.0f}".replace("-", "−")


def _segment(v):
    """Index of the segment that holds v (-1 = below the floor)."""
    return min(SEGMENTS - 1, int((v - RSSI_FLOOR) // T.RSSI_STEP)) if v > RSSI_FLOOR else -1


def paint_meter(p, x, cy, rssi, best=None, dim=False):
    """Segmented bar, 5 dB per segment, lit up to rssi in the heat map's colour
    for that level, with a peak-hold segment at best."""
    lit = _segment(rssi)
    peak = _segment(best) if best is not None else -1
    on = T.signal_color(rssi)
    off = QColor(T.RAISED)
    if dim:
        on.setAlphaF(0.35)
    held = T.signal_color(best) if best is not None else on
    held.setAlphaF(0.3 if dim else 0.55)
    p.setPen(Qt.NoPen)
    y = cy - SEG_H / 2
    for k in range(SEGMENTS):
        p.setBrush(on if k <= max(lit, 0) else held if k == peak else off)
        p.drawRect(QRectF(x + k * (SEG_W + SEG_GAP), y, SEG_W, SEG_H))


class SignalDelegate(QStyledItemDelegate):
    """Signal column: meter, then the dBm value."""

    def paint(self, painter, option, index):
        rssi = index.data(RSSI_ROLE)
        if rssi is None:
            return super().paint(painter, option, index)
        self.initStyleOption(option, index)
        option.text = ""
        style = option.widget.style() if option.widget else None
        if style:  # selection/hover background only
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        stale = bool(index.data(STALE_ROLE))
        r = option.rect.adjusted(8, 0, -8, 0)
        painter.save()
        paint_meter(painter, r.x(), r.center().y() + 0.5, rssi, index.data(BEST_ROLE), stale)
        painter.setFont(T.tabular(option.font))
        painter.setPen(QColor(T.FAINT if stale else T.INK))
        painter.drawText(r.adjusted(METER_W + 8, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, dbm(rssi))
        painter.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setWidth(METER_W + 60)
        return s


def column_menu(table, key, hidden_default=()):
    """Right-click the header to show/hide columns; remembered in QSettings."""
    settings = QSettings("esp32-ai", "scanner")
    model = table.model()
    hdr = table.horizontalHeader()
    stored = settings.value(f"hidden/{key}")
    hidden = set(stored) if isinstance(stored, list) else (
        {stored} if isinstance(stored, str) else set(hidden_default))
    titles = [model.headerData(i, Qt.Horizontal) for i in range(model.columnCount())]
    for i, t in enumerate(titles):
        table.setColumnHidden(i, t in hidden)

    def show_menu(pos):
        m = QMenu(table)
        m.addSection("Columns")
        for i, t in enumerate(titles):
            a = m.addAction(t)
            a.setCheckable(True)
            a.setChecked(not table.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: toggle(i, on))
        m.exec(hdr.mapToGlobal(pos))

    def toggle(i, on):
        table.setColumnHidden(i, not on)
        settings.setValue(f"hidden/{key}", [t for j, t in enumerate(titles)
                                             if table.isColumnHidden(j)] or "")

    hdr.setContextMenuPolicy(Qt.CustomContextMenu)
    hdr.customContextMenuRequested.connect(show_menu)
    hdr.setToolTip("Right-click to choose columns")


def _plot_defaults(plot):
    plot.setBackground(T.GROUND)
    plot.showGrid(x=False, y=True, alpha=0.08)
    plot.setYRange(RSSI_FLOOR, RSSI_CEIL, padding=0.02)
    plot.setLabel("left", "dBm")
    plot.setMouseEnabled(x=False, y=False)
    plot.hideButtons()
    plot.setMenuEnabled(False)
    for name in ("left", "bottom"):
        ax = plot.getAxis(name)
        ax.setPen(pg.mkPen(T.LINE))
        ax.setTextPen(pg.mkPen(T.MUTED))
        ax.setStyle(tickFont=T.tabular(T.ui_font(9)), tickLength=-4)


class SignalHistory(pg.PlotWidget):
    """RSSI over time for a set of devices."""

    WINDOW_S = 600

    def __init__(self, label_fn):
        super().__init__(axisItems={"bottom": pg.DateAxisItem()})
        self.label_fn = label_fn
        _plot_defaults(self)
        self.legend = self.addLegend(offset=(-10, 10), labelTextSize="9pt",
                                     labelTextColor=T.MUTED, brush=pg.mkBrush(T.GROUND + "cc"))

    def show_records(self, records):
        self.clear()
        self.legend.clear()
        now = time.time()
        start = now - 60
        # Colours by position in a stable (key-sorted) order, so the same set of
        # devices keeps its colours from one refresh to the next.
        order = {r.key: i for i, r in enumerate(sorted(records, key=lambda r: r.key))}
        for rec in records:
            pts = [(t, v) for t, v in rec.history if now - t <= self.WINDOW_S]
            if not pts:
                continue
            xs, ys = zip(*pts)
            start = min(start, xs[0] - 5)
            color = QColor(T.SERIES[order[rec.key] % len(T.SERIES)])
            self.plot(xs, ys, pen=pg.mkPen(color, width=2), symbol="o", symbolSize=4,
                      symbolBrush=color, symbolPen=None, name=self.label_fn(rec))
        # Grow the window with the data, up to WINDOW_S.
        self.setXRange(max(start, now - self.WINDOW_S), now + 5, padding=0)


class ChannelMap(pg.PlotWidget):
    """Classic WiFi-analyser view: each network is an arch spanning its
    20 MHz channel (±2 channel numbers), peaking at its RSSI. Networks are
    drawn quietly; the strongest per channel takes its signal colour, and
    selected ones are outlined in the accent."""

    def __init__(self):
        super().__init__()
        _plot_defaults(self)
        self.setLabel("bottom", "Channel, 2.4 GHz")
        self.setXRange(-1, 15, padding=0)
        self.getAxis("bottom").setTicks([[(c, str(c)) for c in range(1, 14)]])

    def show_networks(self, records, highlight=()):
        self.clear()
        highlight = set(highlight)
        xs_unit = np.linspace(-2, 2, 41)
        shape = 1 - (xs_unit / 2) ** 2
        # Label only the strongest network per channel (plus any selected
        # ones); labelling all of them is an unreadable pile-up.
        strongest = {}
        for rec in records:
            ch = rec.d.get("ch")
            if ch and (ch not in strongest or rec.rssi > strongest[ch].rssi):
                strongest[ch] = rec
        top = {r.key for r in strongest.values()}
        quiet = QColor(T.MUTED)
        for rec in sorted(records, key=lambda r: (rec_rank(r, top, highlight), r.rssi)):
            ch = rec.d.get("ch")
            if not ch:
                continue
            ys = RSSI_FLOOR + (rec.rssi - RSSI_FLOOR) * shape
            if rec.key in highlight:
                line, width, fill_a = QColor(T.ACCENT), 2.5, 0.22
            elif rec.key in top:
                line, width, fill_a = T.signal_color(rec.rssi), 1.8, 0.14
            else:
                line, width, fill_a = QColor(quiet), 1.0, 0.03
                line.setAlphaF(0.45)
            fill = QColor(line)
            fill.setAlphaF(fill_a)
            self.plot(xs_unit + ch, ys, pen=pg.mkPen(line, width=width),
                      fillLevel=RSSI_FLOOR, brush=fill)
            if rec.key in top or rec.key in highlight:
                label = pg.TextItem(rec.d.get("ssid") or "(hidden)",
                                    color=T.ACCENT if rec.key in highlight else T.INK,
                                    anchor=(0.5, 1.15))
                label.setFont(T.ui_font(9))
                label.setPos(ch, rec.rssi)
                self.addItem(label)


def rec_rank(rec, top, highlight):
    """Draw order: quiet arches first, then the strongest, then selected on top."""
    return 2 if rec.key in highlight else 1 if rec.key in top else 0
