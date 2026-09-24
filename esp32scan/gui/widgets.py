"""Custom widgets: signal-strength cell delegate and the two charts."""

import time
import zlib

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from .models import RSSI_ROLE

RSSI_FLOOR = -100
RSSI_CEIL = -30


def rssi_color(rssi):
    if rssi >= -60:
        return QColor("#3fb950")
    if rssi >= -75:
        return QColor("#d29922")
    return QColor("#f85149")


def key_color(key):
    """A stable, distinct colour per device."""
    return pg.intColor(zlib.crc32(key.encode()) % 24, hues=24, values=1, maxValue=230)


class RssiDelegate(QStyledItemDelegate):
    """Draws the signal column as a coloured bar with the dBm value on top."""

    def paint(self, painter, option, index):
        rssi = index.data(RSSI_ROLE)
        if rssi is None:
            return super().paint(painter, option, index)
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else None
        if style:
            # selection/hover background only
            option.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)

        frac = (rssi - RSSI_FLOOR) / (RSSI_CEIL - RSSI_FLOOR)
        frac = max(0.03, min(1.0, frac))
        r = option.rect.adjusted(4, 5, -4, -5)
        bar = QRectF(r.x(), r.y(), r.width() * frac, r.height())
        color = rssi_color(rssi)
        if index.data(Qt.ForegroundRole) is not None:  # stale row
            color.setAlpha(70)
        else:
            color.setAlpha(170)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(bar, 3, 3)
        painter.setPen(option.palette.text().color())
        painter.drawText(r.adjusted(6, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         index.data(Qt.DisplayRole))
        painter.restore()


def _plot_defaults(plot):
    plot.setBackground(None)
    plot.showGrid(x=True, y=True, alpha=0.15)
    plot.setYRange(RSSI_FLOOR, RSSI_CEIL, padding=0.02)
    plot.setLabel("left", "Signal", units="dBm")
    plot.setMouseEnabled(x=False, y=False)
    plot.hideButtons()
    plot.setMenuEnabled(False)


class SignalHistory(pg.PlotWidget):
    """RSSI over time for a set of devices."""

    WINDOW_S = 600

    def __init__(self, label_fn):
        super().__init__(axisItems={"bottom": pg.DateAxisItem()})
        self.label_fn = label_fn
        _plot_defaults(self)
        self.legend = self.addLegend(offset=(-10, 10), labelTextSize="8pt")

    def show_records(self, records):
        self.clear()
        self.legend.clear()
        now = time.time()
        start = now - 60
        for rec in records:
            pts = [(t, v) for t, v in rec.history if now - t <= self.WINDOW_S]
            if not pts:
                continue
            xs, ys = zip(*pts)
            start = min(start, xs[0] - 5)
            color = key_color(rec.key)
            self.plot(xs, ys, pen=pg.mkPen(color, width=2), symbol="o", symbolSize=5,
                      symbolBrush=color, symbolPen=None, name=self.label_fn(rec))
        # Grow the window with the data, up to WINDOW_S.
        self.setXRange(max(start, now - self.WINDOW_S), now + 5, padding=0)


class ChannelMap(pg.PlotWidget):
    """Classic WiFi-analyser view: each network is an arch spanning its
    20 MHz channel (±2 channel numbers), peaking at its RSSI."""

    def __init__(self):
        super().__init__()
        _plot_defaults(self)
        self.setLabel("bottom", "Channel (2.4 GHz)")
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
        labelled = {r.key for r in strongest.values()} | highlight
        for rec in sorted(records, key=lambda r: r.rssi):
            ch = rec.d.get("ch")
            if not ch:
                continue
            ys = RSSI_FLOOR + (rec.rssi - RSSI_FLOOR) * shape
            color = key_color(rec.key)
            fill = QColor(color)
            hot = rec.key in highlight
            fill.setAlpha(90 if hot else 35)
            self.plot(xs_unit + ch, ys, pen=pg.mkPen(color, width=3 if hot else 1.5),
                      fillLevel=RSSI_FLOOR, brush=fill)
            if rec.key not in labelled:
                continue
            label = pg.TextItem(rec.d.get("ssid") or "(hidden)", color=color, anchor=(0.5, 1))
            label.setPos(ch, rec.rssi)
            self.addItem(label)
