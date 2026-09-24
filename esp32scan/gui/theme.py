"""Look and feel: colour tokens, fonts, the Qt palette + stylesheet, and the
signal colour ramp shared by the tables, the meter and the heat map.

The accent is cyan on purpose: it sits outside both data ramps (red→green
signal, plasma counts), so UI state is never mistaken for a measurement.
"""

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from .. import survey as sv

GROUND = "#182028"   # window background
PANEL = "#1F2932"    # rail, headers, cards
RAISED = "#283440"   # inputs, hover, unlit meter segments
LINE = "#33414D"     # hairlines
INK = "#E4EAEE"      # text
MUTED = "#8A9AA6"    # secondary text
FAINT = "#5B6B77"    # stale rows, disabled
ACCENT = "#49C6E5"   # selection, active, "here", capture
WARN = "#EF8A5B"

UI_FAMILY = "Barlow Semi Condensed"
MONO_FAMILY = "Azeret Mono"

# Signal meter: one segment per 5 dB from -100 to -30 dBm.
RSSI_FLOOR, RSSI_CEIL, RSSI_STEP = -100, -30, 5
# Same scale as the heat map's default signal range, so a colour means the
# same dBm in a table as on the floor plan.
RAMP_MIN, RAMP_MAX = -90, -40

_FONTS = Path(__file__).with_name("fonts")


def load_fonts():
    for f in sorted(_FONTS.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


def ui_font(size=10.0, weight=QFont.Weight.Normal):
    f = QFont(UI_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def mono_font(size=9.0):
    f = QFont(MONO_FAMILY)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(size)
    return f


def tabular(font):
    """Same-width digits so numbers line up in columns."""
    f = QFont(font)
    f.setFeature(QFont.Tag("tnum"), 1)
    return f


def _mix(a, b, t):
    return QColor.fromRgbF(*(a[i] + (b[i] - a[i]) * t for i in range(3)))


_RAMP = [QColor(c).getRgbF()[:3] for c in sv.SIGNAL_STOPS]


def signal_color(rssi):
    """The heat map's weak→strong ramp, as a QColor."""
    t = (rssi - RAMP_MIN) / (RAMP_MAX - RAMP_MIN)
    t = max(0.0, min(1.0, t)) * (len(_RAMP) - 1)
    i = min(int(t), len(_RAMP) - 2)
    return _mix(_RAMP[i], _RAMP[i + 1], t - i)


# Categorical colours for lines in the signal-history chart (never a signal level).
SERIES = ["#49C6E5", "#F2B84B", "#E36BAE", "#8FD694", "#B39DFF", "#FF8A5B",
          "#5EA8FF", "#D8D0A0"]


def palette():
    p = QPalette()
    c = QColor
    p.setColor(QPalette.Window, c(GROUND))
    p.setColor(QPalette.WindowText, c(INK))
    p.setColor(QPalette.Base, c(GROUND))
    p.setColor(QPalette.AlternateBase, c(PANEL))
    p.setColor(QPalette.Text, c(INK))
    p.setColor(QPalette.Button, c(RAISED))
    p.setColor(QPalette.ButtonText, c(INK))
    p.setColor(QPalette.ToolTipBase, c(PANEL))
    p.setColor(QPalette.ToolTipText, c(INK))
    hl = c(ACCENT)
    hl.setAlpha(60)
    p.setColor(QPalette.Highlight, hl)
    p.setColor(QPalette.HighlightedText, c(INK))
    p.setColor(QPalette.PlaceholderText, c(FAINT))
    p.setColor(QPalette.Link, c(ACCENT))
    p.setColor(QPalette.Mid, c(LINE))
    p.setColor(QPalette.Dark, c(LINE))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, c(FAINT))
    return p


STYLE = f"""
* {{ outline: none; }}
QMainWindow, QDialog {{ background: {GROUND}; }}
QToolTip {{ background: {PANEL}; color: {INK}; border: 1px solid {LINE}; padding: 4px 6px; }}

/* --- rail ------------------------------------------------------------ */
#rail {{ background: {PANEL}; border-right: 1px solid {LINE}; }}
#railTitle {{ font-size: 13pt; font-weight: 600; color: {INK}; padding: 2px 4px; }}
#railSection {{ color: {MUTED}; font-size: 10pt; font-weight: 500; }}
#railNote {{ color: {MUTED}; font-size: 9.5pt; }}
#side {{ background: {GROUND}; border-right: 1px solid {LINE}; }}
#side QListView {{ background: transparent; }}
#dot {{ border-radius: 5px; background: {FAINT}; }}
#dot[state="on"] {{ background: {ACCENT}; }}
#dot[state="busy"] {{ background: {WARN}; }}
#railRule {{ background: {LINE}; max-height: 1px; min-height: 1px; }}

/* --- inputs ---------------------------------------------------------- */
QLineEdit, QComboBox, QPlainTextEdit {{
    background: {RAISED}; color: {INK}; border: 1px solid transparent;
    border-radius: 4px; padding: 4px 8px; selection-background-color: {ACCENT};
    selection-color: {GROUND};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE};
    selection-background-color: {RAISED}; selection-color: {INK}; }}

QPushButton, QToolButton {{
    background: transparent; color: {INK}; border: 1px solid {LINE};
    border-radius: 4px; padding: 4px 12px;
}}
QPushButton:hover, QToolButton:hover {{ background: {RAISED}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {FAINT}; border-color: {RAISED}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: {GROUND}; border-color: {ACCENT};
    font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: #6FD4EC; }}
QPushButton[primary="true"]:disabled {{ background: {RAISED}; color: {FAINT};
    border-color: {RAISED}; }}
QPushButton[flat="true"], QToolButton[flat="true"] {{ border-color: transparent; color: {MUTED}; }}
QPushButton[flat="true"]:hover, QToolButton[flat="true"]:hover {{ color: {INK}; }}

/* segmented control: checkable buttons in a row */
QToolButton[segment] {{ border-radius: 0; padding: 4px 10px; color: {MUTED}; border-color: {LINE}; }}
QToolButton[segment="first"] {{ border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}
QToolButton[segment="last"] {{ border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
QToolButton[segment]:checked {{ background: {RAISED}; color: {INK}; border-color: {ACCENT}; }}

/* filter chips */
QToolButton[chip="true"] {{ border-radius: 12px; padding: 3px 12px; color: {MUTED}; }}
QToolButton[chip="true"]:checked {{ color: {INK}; border-color: {ACCENT}; background: {RAISED}; }}

QCheckBox {{ color: {INK}; spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {MUTED};
    border-radius: 3px; background: transparent; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: none; }}
QCheckBox:focus {{ color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {RAISED}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {MUTED}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {INK}; width: 12px; height: 12px;
    margin: -4px 0; border-radius: 6px; }}
QSlider::handle:horizontal:focus {{ background: {ACCENT}; }}

/* --- tables and lists ------------------------------------------------ */
QTableView, QListView {{ background: {GROUND}; border: none; gridline-color: transparent;
    selection-background-color: rgba(73, 198, 229, 0.16); selection-color: {INK}; }}
QTableView::item {{ padding: 0 8px; border: none; }}
QTableView::item:hover {{ background: {PANEL}; }}
QTableView::item:selected {{ background: rgba(73, 198, 229, 0.16); }}
QHeaderView {{ background: {GROUND}; border: none; }}
QHeaderView::section {{ background: {GROUND}; color: {MUTED}; border: none;
    border-bottom: 1px solid {LINE}; padding: 6px 8px; font-weight: 500; }}
QHeaderView::section:hover {{ color: {INK}; }}
QTableCornerButton::section {{ background: {GROUND}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {RAISED}; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {LINE}; }}
QScrollBar::handle:vertical {{ min-height: 30px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {LINE}; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:horizontal {{ width: 1px; }}

/* chart tabs: plain text tabs, accent underline on the current one */
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 6px 2px; margin-right: 18px;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {INK}; }}

/* --- page furniture -------------------------------------------------- */
#pageBar {{ background: {GROUND}; }}
#count {{ color: {MUTED}; }}
#card {{ background: {PANEL}; border-radius: 6px; border-left: 3px solid {LINE}; }}
#card[state="capturing"] {{ background: #1C3440; border-left: 3px solid {ACCENT}; }}
#card[state="next"] {{ border-left: 3px solid {ACCENT}; }}
#card[state="warn"] {{ border-left: 3px solid {WARN}; }}
#cardTitle {{ font-size: 12pt; font-weight: 600; }}
#cardBody {{ color: {MUTED}; }}
#heading {{ font-size: 16pt; font-weight: 600; }}
#subheading {{ color: {MUTED}; }}
#fieldLabel {{ color: {MUTED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 4px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::indicator:checked {{ background: {ACCENT}; border-radius: 2px; width: 10px; height: 10px;
    margin-left: 4px; }}
"""


def apply(app):
    from . import models
    load_fonts()
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(ui_font(10.5))
    app.setStyleSheet(STYLE)
    models.init_fonts()
