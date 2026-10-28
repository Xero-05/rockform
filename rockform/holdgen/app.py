"""Hold generator window: choose a type and difficulty, inspect grip as a heat map, see how difficulty
changes with the angle of pull, and export STL."""
from __future__ import annotations

import json
import random
import sys
import numpy as np
import pyvista as pv
from matplotlib.colors import LinearSegmentedColormap
from pyvistaqt import QtInteractor
from PySide6 import QtCore, QtGui, QtWidgets
from ..widgets import STYLE, label, button
from ..geometry import validate_solid
from .shapes import HOLD_TYPES, HOLD_INFO, SIZES, build
from .holding import orientation
from .generate import HoldRequest, generate, parse_description, measure, best_mode, pull_range, GRAB_STYLES, GRAB_NAMES

EXTRA_STYLE = """
QToolButton#holdType { background: #18282f; border: 1px solid #2d4147; border-radius: 8px; padding: 4px;
                       color: #c9d6d4; font-size: 12px; }
QToolButton#holdType:hover { border-color: #73c8b4; }
QToolButton#holdType:checked { background: #29483f; border: 2px solid #9edbc1; color: #ecfff8; font-weight: 650; }
QPushButton#grab { text-align: left; padding: 6px 8px; font-size: 12px; }
QPushButton#grab:checked { background: #29483f; border: 1px solid #9edbc1; }
QPushButton#size { padding: 6px 4px; }
QPushButton#size:checked { background: #29483f; border: 2px solid #9edbc1; color: #ecfff8; font-weight: 650; }
QFrame#card { background: #15242b; border: 1px solid #2d4147; border-radius: 8px; }
QLabel#big { font-size: 34px; font-weight: 700; color: #f3f5ed; }
QLabel#value { color: #b8f0d8; font-weight: 650; min-width: 92px; }
QSlider::groove:horizontal { height: 6px; background: #26373e; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #407c67; border-radius: 3px; }
QSlider::handle:horizontal { background: #9edbc1; width: 16px; margin: -6px 0; border-radius: 8px; }
"""
# Difficulty colours: easy green, moderate amber, hard red.
DIFFICULTY_STOPS = np.array([[64, 178, 120], [150, 200, 90], [240, 196, 70], [236, 120, 60], [205, 52, 62]], float)
WORDS = ((2, "Very easy"), (4, "Easy"), (6, "Moderate"), (8, "Hard"), (10.01, "Very hard"))
# Grip heat map: rock grey where nothing holds, then cold to hot as the surface resists the pull better.
GRIP_CMAP = LinearSegmentedColormap.from_list("grip", [(0., "#5f7073"), (.02, "#2c4fa3"), (.3, "#1fa3c9"),
                                                       (.55, "#5ed17a"), (.75, "#f4d03f"), (.9, "#f0812e"), (1., "#d7263d")])
VIEWS = {"pinch": "top"}   # pinches read best from above; the rest from the side


def difficulty_color(d):
    t = float(np.clip(d / 10., 0., 1.)) * (len(DIFFICULTY_STOPS) - 1)
    i = min(int(t), len(DIFFICULTY_STOPS) - 2)
    return QtGui.QColor(*(DIFFICULTY_STOPS[i] + (DIFFICULTY_STOPS[i + 1] - DIFFICULTY_STOPS[i]) * (t - i)).astype(int))


def difficulty_word(d):
    return next(word for limit, word in WORDS if d < limit)


def wall_text(angle):
    if abs(angle) < .5:
        return "Vertical"
    if angle >= 89.5:
        return "Roof"
    return f"{abs(angle):.0f}° {'overhang' if angle > 0 else 'slab'}"


def rotation_text(angle):
    if abs(angle) < .5:
        return "As designed"
    if abs(abs(angle) - 180) < .5:
        return "Upside down"
    return f"{abs(angle):.0f}° {'anticlockwise' if angle > 0 else 'clockwise'}"


def type_icon(hold_type, size=QtCore.QSize(76, 54)):
    """Silhouette of the real generated cross-section: side view with the wall on the left
    (pinch: top view with the wall below)."""
    mesh, _ = build(hold_type, .35, seed=0)
    top = VIEWS.get(hold_type) == "top"
    section = mesh.section(plane_origin=(0, 0, 0), plane_normal=(0, 1, 0) if top else (1, 0, 0))
    loop = max(section.discrete, key=len)
    pts = loop[:, [0, 2]] if top else loop[:, [2, 1]]          # (across, up) in icon space
    pixmap = QtGui.QPixmap(size)
    pixmap.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pixmap)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    margin = 6
    span = np.ptp(pts, axis=0).max()
    scale = (min(size.width(), size.height()) - 2 * margin) / span
    lo = pts.min(axis=0)
    w, h = np.ptp(pts, axis=0) * scale
    ox = (size.width() - w) / 2 if top else margin + 4
    oy = (size.height() + h) / 2
    poly = QtGui.QPolygonF([QtCore.QPointF(ox + (x - lo[0]) * scale, oy - (y - lo[1]) * scale) for x, y in pts])
    p.setPen(QtGui.QPen(QtGui.QColor("#7f9796"), 2))
    if top:
        p.drawLine(QtCore.QPointF(4, oy + 1), QtCore.QPointF(size.width() - 4, oy + 1))
    else:
        p.drawLine(QtCore.QPointF(ox - 1, 3), QtCore.QPointF(ox - 1, size.height() - 3))
    p.setPen(QtGui.QPen(QtGui.QColor("#d9f5ea"), 1.4))
    p.setBrush(QtGui.QColor("#5fae96"))
    p.drawPolygon(poly)
    p.end()
    return QtGui.QIcon(pixmap)


class RangeBar(QtWidgets.QWidget):
    """0-10 difficulty track: band = range across pull angles, dot = straight-down pull."""

    def __init__(self):
        super().__init__()
        self.values = None
        self.setMinimumHeight(38)

    def show_range(self, nominal, low, high):
        self.values = nominal, low, high
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        x0, x1, y = 8., self.width() - 8., 12.
        gradient = QtGui.QLinearGradient(x0, 0, x1, 0)
        for k in range(11):
            gradient.setColorAt(k / 10, difficulty_color(k))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#26373e"))
        p.drawRoundedRect(QtCore.QRectF(x0, y - 3, x1 - x0, 6), 3, 3)
        p.setPen(QtGui.QColor("#7f9796"))
        for k in (0, 5, 10):
            x = x0 + (x1 - x0) * k / 10
            p.drawText(QtCore.QRectF(x - 12, y + 9, 24, 14), QtCore.Qt.AlignCenter, str(k))
        if self.values is None:
            return
        nominal, low, high = self.values
        a, b = x0 + (x1 - x0) * low / 10, x0 + (x1 - x0) * high / 10
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(gradient)
        p.drawRoundedRect(QtCore.QRectF(a - 3, y - 6, max(b - a, 0) + 6, 12), 6, 6)
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
        p.setBrush(difficulty_color(nominal))
        p.drawEllipse(QtCore.QPointF(x0 + (x1 - x0) * nominal / 10, y), 6, 6)


class PullChart(QtWidgets.QWidget):
    """Difficulty for each pull direction: across = direction around the hold (0 = down, +-90 = side-pulls,
    +-180 = undercling), up = pull away from the wall. Click a cell to show its heat map."""
    picked = QtCore.Signal(float, float)
    GUIDES = ((-180, "Under"), (-90, "Side ←"), (0, "Down"), (90, "Side →"), (180, "Under"))

    def __init__(self):
        super().__init__()
        self.pull, self.selected = None, (0., 0.)
        self.setMinimumSize(300, 170)
        self.setMouseTracking(True)

    def show_pull(self, pull, selected=(0., 0.)):
        self.pull, self.selected = pull, selected
        self.update()

    def _plot_rect(self):
        return QtCore.QRectF(34, 8, self.width() - 44, self.height() - 40)

    def _cell(self, pos):
        r = self._plot_rect()
        around, out = self.pull["around_deg"], self.pull["outward_deg"]
        j = int((pos.x() - r.left()) / r.width() * len(around))
        i = int((r.bottom() - pos.y()) / r.height() * len(out))
        return (i, j) if 0 <= i < len(out) and 0 <= j < len(around) else None

    def _xy(self, a, o):
        r = self._plot_rect()
        around, out = np.array(self.pull["around_deg"]), np.array(self.pull["outward_deg"])
        cw, ch = r.width() / len(around), r.height() / len(out)
        return QtCore.QPointF(r.left() + (a - around[0]) / np.ptp(around) * (r.width() - cw) + cw / 2,
                              r.bottom() - (o - out[0]) / np.ptp(out) * (r.height() - ch) - ch / 2)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if not self.pull:
            return
        r = self._plot_rect()
        around, out = np.array(self.pull["around_deg"]), np.array(self.pull["outward_deg"])
        grid = np.array(self.pull["difficulty"])
        cw, ch = r.width() / len(around), r.height() / len(out)
        p.setPen(QtCore.Qt.NoPen)
        for i in range(len(out)):
            for j in range(len(around)):
                p.setBrush(difficulty_color(grid[i, j]))
                p.drawRect(QtCore.QRectF(r.left() + j * cw, r.bottom() - (i + 1) * ch, cw + .5, ch + .5))
        # Boundary of the pull cone used for the reported range.
        cone = np.radians(self.pull["cone_deg"])
        s = np.linspace(-cone, cone, 41)
        o = np.degrees(np.arccos(np.clip(np.cos(cone) / np.cos(s), -1, 1)))
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 1.5, QtCore.Qt.DashLine))
        p.drawPolyline(QtGui.QPolygonF([self._xy(np.degrees(a), b) for a, b in zip(s, o)]))
        f = p.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        for a, name in self.GUIDES:
            x = self._xy(a, 0).x()
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 60), 1))
            p.drawLine(QtCore.QPointF(x, r.top()), QtCore.QPointF(x, r.bottom()))
            p.setPen(QtGui.QColor("#98aaa9"))
            p.drawText(QtCore.QRectF(x - 28, r.bottom() + 2, 56, 14), QtCore.Qt.AlignCenter, name)
        p.drawText(QtCore.QRectF(r.left(), r.bottom() + 16, r.width(), 14), QtCore.Qt.AlignCenter,
                   "direction of pull around the hold")
        for value in (out[0], out[-1]):
            pt = self._xy(around[0], value)
            p.drawText(QtCore.QRectF(0, pt.y() - 7, 30, 14), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{value:.0f}°")
        p.save()
        p.translate(9, r.center().y())
        p.rotate(-90)
        p.drawText(QtCore.QRectF(-60, -6, 120, 14), QtCore.Qt.AlignCenter, "away from wall →")
        p.restore()
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(self._xy(*self.selected), 5, 5)

    def mouseMoveEvent(self, event):
        cell = self.pull and self._cell(event.position())
        if cell:
            i, j = cell
            a, o = self.pull["around_deg"][j], self.pull["outward_deg"][i]
            QtWidgets.QToolTip.showText(event.globalPosition().toPoint(),
                                        f"{pull_text(a)}, {o:.0f}° from the wall: difficulty "
                                        f"{self.pull['difficulty'][i][j]:.1f}\nClick to show its heat map", self)

    def mousePressEvent(self, event):
        cell = self.pull and self._cell(event.position())
        if cell:
            i, j = cell
            self.picked.emit(float(self.pull["around_deg"][j]), float(self.pull["outward_deg"][i]))


def pull_text(a):
    a = (a + 180) % 360 - 180
    if abs(a) < .5:
        return "Pulling straight down"
    if abs(abs(a) - 180) < .5:
        return "Undercling (pulling up)"
    return f"Pulling {abs(a):.0f}° {'right' if a > 0 else 'left'} of down"


def grab_arrow(vector, wall, around, outward):
    """Show a fixed-world grip vector against the hold as drawn on its wall, for one pull direction."""
    return orientation(wall) @ orientation(0., around) @ orientation(-(wall + outward)) @ np.asarray(vector)


def card(title):
    frame = QtWidgets.QFrame()
    frame.setObjectName("card")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.addWidget(label(title, "eyebrow"))
    return frame, layout


def slider(lo, hi, value, step=1):
    s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setPageStep(step * 5)
    s.setValue(value)
    return s


class GeneratorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rockform hold generator")
        self.resize(1380, 880)
        self.hold, self.shown_type, self.pull_dir, self.pull = None, None, (0., 0.), None
        self.placement_timer = QtCore.QTimer(self, singleShot=True, interval=150)
        self.placement_timer.timeout.connect(self.refresh_placement)
        self.debounce = QtCore.QTimer(self, singleShot=True, interval=300)
        self.debounce.timeout.connect(self.from_controls)

        side = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(side)
        form.setSpacing(8)
        form.addWidget(label("ROCKFORM", "eyebrow"))
        form.addWidget(label("Hold generator", "brand"))

        form.addWidget(label("1  Hold type", "section"))
        types = QtWidgets.QGridLayout()
        self.type_group = QtWidgets.QButtonGroup(self)
        for i, kind in enumerate(HOLD_TYPES):
            b = QtWidgets.QToolButton()
            b.setObjectName("holdType")
            b.setText(kind.capitalize())
            b.setIcon(type_icon(kind))
            b.setIconSize(QtCore.QSize(76, 54))
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setFixedSize(100, 88)
            b.setToolTip(HOLD_INFO[kind])
            self.type_group.addButton(b, i)
            types.addWidget(b, i // 3, i % 3)
        self.type_group.button(0).setChecked(True)
        self.type_group.idClicked.connect(self.type_changed)
        form.addLayout(types)
        self.type_info = label(HOLD_INFO[HOLD_TYPES[0]])
        form.addWidget(self.type_info)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(label("Size"))
        self.size_group = QtWidgets.QButtonGroup(self)
        for i, name in enumerate(SIZES):
            b = QtWidgets.QPushButton(name)
            b.setObjectName("size")
            b.setCheckable(True)
            b.setToolTip(f"{name}: body × {SIZES[name]:g}. Finger-sized features (edge depth, pocket hole, "
                         "pinch gap) keep their size.")
            self.size_group.addButton(b, i)
            row.addWidget(b)
        self.size_group.button(list(SIZES).index("M")).setChecked(True)
        self.size_group.idClicked.connect(lambda _: self.from_controls())
        form.addLayout(row)
        self.size_info = label("")
        form.addWidget(self.size_info)

        form.addWidget(label("2  Difficulty", "section"))
        row = QtWidgets.QHBoxLayout()
        self.difficulty = slider(0, 100, 30)
        self.difficulty_value = label("", "value")
        row.addWidget(self.difficulty, 1)
        row.addWidget(self.difficulty_value)
        form.addLayout(row)
        ends = QtWidgets.QHBoxLayout()
        ends.addWidget(label("Easy: lots of positive surface"))
        ends.addStretch()
        ends.addWidget(label("Hard: very little"))
        form.addLayout(ends)
        self.reach = label("")
        form.addWidget(self.reach)

        form.addWidget(label("3  Wall and placement", "section"))
        row = QtWidgets.QHBoxLayout()
        self.wall = slider(-30, 90, 0, 5)
        self.wall_value = label("", "value")
        row.addWidget(self.wall, 1)
        row.addWidget(self.wall_value)
        form.addLayout(row)
        presets = QtWidgets.QHBoxLayout()
        for name, angle in (("Slab", -10), ("Vertical", 0), ("30°", 30), ("45°", 45), ("Roof", 90)):
            presets.addWidget(button(name, lambda _=False, a=angle: self.wall.setValue(a)))
        form.addLayout(presets)
        form.addWidget(label("Rotation on the wall: turns the bolted hold while gravity stays put. "
                             "The shape does not change."))
        row = QtWidgets.QHBoxLayout()
        self.rotation = slider(-180, 180, 0, 5)
        self.rotation_value = label("", "value")
        row.addWidget(self.rotation, 1)
        row.addWidget(self.rotation_value)
        form.addLayout(row)
        presets = QtWidgets.QHBoxLayout()
        for name, angle in (("↻90°", -90), ("↻45°", -45), ("Upright", 0), ("↺45°", 45), ("↺90°", 90)):
            presets.addWidget(button(name, lambda _=False, a=angle: self.rotation.setValue(a)))
        form.addLayout(presets)

        form.addWidget(label("4  Variation", "section"))
        row = QtWidgets.QHBoxLayout()
        self.seed = QtWidgets.QLineEdit("1")
        self.seed.setMaximumWidth(110)
        self.seed.editingFinished.connect(self.from_controls)
        row.addWidget(self.seed)
        row.addWidget(button("New variation", self.randomise, primary=True), 1)
        form.addLayout(row)

        describe = QtWidgets.QGroupBox("Or describe it")
        d = QtWidgets.QVBoxLayout(describe)
        self.description = QtWidgets.QLineEdit()
        self.description.setPlaceholderText("e.g. hard crimp on a 30 degree overhang")
        self.description.returnPressed.connect(self.from_description)
        d.addWidget(self.description)
        d.addWidget(button("Generate from description", self.from_description))
        form.addWidget(describe)
        self.message = label("")
        form.addWidget(self.message)
        form.addStretch()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(360)

        # Viewer with overlay options.
        self.plotter = QtInteractor(self, auto_update=False)
        self.plotter.set_background("#17272e", top="#253f45")
        options = QtWidgets.QHBoxLayout()
        self.view_title = label("", "section")
        options.addWidget(self.view_title)
        options.addSpacing(16)
        options.addWidget(label("Overlay"))
        self.overlay = QtWidgets.QComboBox()
        self.overlay.addItems(["Grip quality: every surface", "Scored area: one hand's grip", "Plain"])
        self.overlay.setToolTip("Grip quality: how well every surface resists the selected pull, wherever it is.\n"
                                "Scored area: only the best hand-width band (35 mm thumb band for pinches);\n"
                                "its weighted area is what the difficulty score is computed from.")
        self.overlay.currentIndexChanged.connect(lambda _: self.draw(keep_camera=True))
        options.addWidget(self.overlay)
        self.arrows = QtWidgets.QCheckBox("Pull arrows")
        self.arrows.setChecked(True)
        self.arrows.toggled.connect(lambda _: self.draw(keep_camera=True))
        options.addWidget(self.arrows)
        options.addStretch()
        options.addWidget(button("Reset view", lambda: self.draw(keep_camera=False)))
        viewer = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(viewer)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(options)
        v.addWidget(self.plotter.interactor, 1)

        # Results strip.
        results = QtWidgets.QHBoxLayout()
        frame, c = card("DIFFICULTY")
        top = QtWidgets.QHBoxLayout()
        self.big = label("–", "big")
        top.addWidget(self.big)
        self.word = label("", "section")
        top.addWidget(self.word, 1)
        c.addLayout(top)
        self.range_bar = RangeBar()
        c.addWidget(self.range_bar)
        self.range_text = label("")
        c.addWidget(self.range_text)
        c.addWidget(label("How it grabs (click to show on the hold):"))
        styles = QtWidgets.QGridLayout()
        self.style_group = QtWidgets.QButtonGroup(self)
        for i, name in enumerate(GRAB_STYLES):
            b = QtWidgets.QPushButton()
            b.setObjectName("grab")
            b.setCheckable(True)
            self.style_group.addButton(b, i)
            styles.addWidget(b, i // 2, i % 2)
        self.style_group.button(0).setChecked(True)
        self.style_group.idClicked.connect(lambda i: self.select_grab(list(GRAB_STYLES.values())[i], 0.))
        c.addLayout(styles)
        c.addStretch()
        frame.setMinimumWidth(300)
        results.addWidget(frame, 1)
        frame, c = card("DIFFICULTY BY ANGLE OF PULL")
        self.chart = PullChart()
        self.chart.picked.connect(self.select_grab)
        c.addWidget(self.chart, 1)
        results.addWidget(frame, 1)
        frame, c = card("HOLD")
        self.details = label("")
        self.details.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        c.addWidget(self.details)
        self.holding_line = label("")
        c.addWidget(self.holding_line)
        c.addStretch()
        self.export_button = button("Export STL + report…", self.export, primary=True)
        self.export_button.setEnabled(False)
        c.addWidget(self.export_button)
        frame.setMinimumWidth(260)
        results.addWidget(frame, 1)
        bottom = QtWidgets.QWidget()
        bottom.setLayout(results)
        bottom.setMaximumHeight(330)

        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(viewer, 1)
        rl.addWidget(bottom)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        layout.addWidget(scroll)
        layout.addWidget(right, 1)
        self.setCentralWidget(central)

        for s in (self.difficulty, self.wall):
            s.valueChanged.connect(self.update_labels)
            s.valueChanged.connect(lambda _: self.debounce.start())
        self.rotation.valueChanged.connect(self.update_labels)
        self.rotation.valueChanged.connect(lambda _: self.placement_timer.start())
        self.update_labels()
        self.from_controls()

    # ---- controls -------------------------------------------------------------------
    def kind(self):
        return HOLD_TYPES[self.type_group.checkedId()]

    def update_labels(self):
        d = self.difficulty.value() / 10
        self.difficulty_value.setText(f"{d:.1f} · {difficulty_word(d)}")
        self.wall_value.setText(wall_text(self.wall.value()))
        r = self.rotation.value()
        self.rotation_value.setText("Upright" if r == 0 else f"{abs(r)}° {'↺' if r > 0 else '↻'}")

    def type_changed(self, index):
        self.type_info.setText(HOLD_INFO[HOLD_TYPES[index]])
        self.from_controls()

    def randomise(self):
        self.seed.setText(str(random.randint(0, 999999)))
        self.from_controls()

    def set_controls(self, request, target):
        widgets = (self.difficulty, self.wall, self.seed, self.type_group, self.size_group)
        for w in widgets:
            w.blockSignals(True)
        self.type_group.button(HOLD_TYPES.index(request.hold_type)).setChecked(True)
        self.type_info.setText(HOLD_INFO[request.hold_type])
        self.difficulty.setValue(round(target * 10))
        self.wall.setValue(round(request.wall_angle))
        self.seed.setText(str(request.seed))
        nearest = min(SIZES, key=lambda k: abs(np.log(SIZES[k] / request.size)))
        self.size_group.button(list(SIZES).index(nearest)).setChecked(True)
        self.custom_size = None if abs(SIZES[nearest] - request.size) < 1e-6 else request.size
        for w in widgets:
            w.blockSignals(False)
        self.update_labels()

    def from_description(self):
        try:
            request = parse_description(self.description.text(), seed=self.seed.text() or 0)
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.run(request)

    def from_controls(self):
        self.debounce.stop()
        size = SIZES[list(SIZES)[self.size_group.checkedId()]]
        if getattr(self, "custom_size", None) and self.sender() is not self.size_group:
            size = self.custom_size        # keep a size typed in a description until a size button is chosen
        self.run(HoldRequest(self.kind(), self.difficulty.value() / 10, None, self.seed.text() or 0,
                             float(self.wall.value()), size=size))

    def run(self, request):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.hold = generate(request)
        except Exception as error:
            self.message.setText(f"Could not generate this hold: {error}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        h = self.hold
        self.set_controls(request, h.target)
        self.message.setText("".join(f"⚠ {n}\n" for n in h.notes).strip())
        self.reach.setText(f"A {request.hold_type} with this variation reaches {h.achievable[0]:.1f}–{h.achievable[1]:.1f} "
                           f"on a {wall_text(request.wall_angle).lower()} wall.")
        size = " × ".join(f"{v:.0f}" for v in h.mesh.extents)
        inches = " × ".join(f"{v / 25.4:.1f}" for v in h.mesh.extents)
        self.size_info.setText(f"{size} mm ({inches} in)" + ("" if request.size == 1 else f" · body × {request.size:.2f}"))
        extra = f"<br>{h.params['fingers']}-finger pocket, {h.params['pocket_depth']:.0f} mm deep" if "fingers" in h.params else ""
        self.details.setText(f"<b>{request.hold_type.capitalize()}</b>, variation {request.seed}<br>{size} mm "
                             f"({inches} in) · "
                             f"{h.mesh.volume / 1000:.0f} cm³<br>Holding area {h.holding_area_mm2:.0f} mm² straight down{extra}")
        self.export_button.setEnabled(True)
        same = self.shown_type == request.hold_type
        self.shown_type = request.hold_type
        self.refresh_placement(keep_camera=same, pull_dir=self.pull_dir if same else (0., 0.))

    def placement(self):
        return float(self.rotation.value())

    def refresh_placement(self, keep_camera=True, pull_dir=None):
        """Re-rate the same shape for its current rotation on the wall (no regeneration)."""
        h = self.hold
        if h is None:
            return
        rot = self.placement()
        self.pull = h.pull if rot == 0 else pull_range(h.mesh, h.request, rotation_deg=rot)
        lo, hi = self.pull["min"], self.pull["max"]
        placed = "" if rot == 0 else f" with the hold turned {rotation_text(rot)}"
        self.range_text.setText(f"<b>{lo:.1f} – {hi:.1f}</b> for pulls within {self.pull['cone_deg']:.0f}° of straight "
                                f"down{placed} (dashed line in the chart).")
        arrows = {"side_right": " →", "side_left": " ←"}
        for i, name in enumerate(GRAB_STYLES):
            d = self.pull["styles"][name]
            b = self.style_group.button(i)
            b.setText(f"{GRAB_NAMES[name].split(' (')[0]}{arrows.get(name, '')}   {d:.1f}")
            b.setStyleSheet(f"border-left: 5px solid {difficulty_color(d).name()};")
            b.setToolTip(f"{GRAB_NAMES[name]}: difficulty {d:.1f}, {difficulty_word(d).lower()}")
        self.select_grab(*(pull_dir or self.pull_dir), keep_camera=keep_camera)

    def select_grab(self, around, outward, keep_camera=True):
        """Show the heat map, arrows and chart marker for one pull direction."""
        self.pull_dir = (around, outward)
        match = [i for i, a in enumerate(GRAB_STYLES.values())
                 if outward == 0 and abs((around - a + 180) % 360 - 180) < .5]
        self.style_group.setExclusive(False)
        for i, b in enumerate(self.style_group.buttons()):
            b.setChecked(bool(match) and i == match[0])
        self.style_group.setExclusive(True)
        if self.hold is not None:
            h = self.hold
            rot = self.placement()
            area, d, _ = measure(h.mesh, h.request, around, outward, rot)
            away = f", {outward:.0f}° from the wall" if outward else ""
            turned = "" if rot == 0 else f" · hold turned {rotation_text(rot)}"
            self.view_title.setText(f"{pull_text(around)}{away}{turned}: difficulty {d:.1f}")
            # The card follows the selected pull and placement; as designed, pulled straight down, is the reference.
            down = abs((around + 180) % 360 - 180) < .5 and outward == 0
            self.big.setText(f"{d:.1f}")
            self.big.setStyleSheet(f"color: {difficulty_color(d).name()};")
            if down and rot == 0:
                note = "straight-down pull"
            else:
                what = "straight-down pull" if down else f"{pull_text(around).lower()}{away}"
                note = f"{what}{turned} · as designed {h.difficulty:.1f}"
            self.word.setText(f"{difficulty_word(d)}<br><span style='font-size:12px;color:#98aaa9'>{note}</span>")
            self.range_bar.show_range(d, self.pull["min"], self.pull["max"])
            how = ""
            if h.request.hold_type == "pinch":
                how = (" · scored as a squeeze" if best_mode(h.mesh, h.request, around, outward, rot) == "squeeze"
                       else " · scored as a ledge: easier than squeezing for this pull")
            self.holding_line.setText(f"Holding area for this pull: {area:.0f} mm²{how}")
            self.chart.show_pull(self.pull, self.pull_dir)
        self.draw(keep_camera=keep_camera)

    # ---- 3D view --------------------------------------------------------------------
    def draw(self, keep_camera=True):
        h, p = self.hold, self.plotter
        if h is None:
            return
        camera = p.camera_position if keep_camera else None
        rotation = orientation(h.request.wall_angle, self.placement())
        mesh = h.mesh.copy()
        mesh.vertices = mesh.vertices @ rotation.T
        surface = pv.wrap(mesh)
        # clear() would also delete the lights and leave every later frame unshaded.
        p.clear_actors()
        for title in list(p.scalar_bars.keys()):
            p.remove_scalar_bar(title)
        mode = self.overlay.currentIndex()
        # Smooth shading with sharp edges kept, a silhouette and crease lines make rims and pockets readable.
        look = dict(smooth_shading=True, split_sharp_edges=True, feature_angle=35., specular=.25, specular_power=20.,
                    silhouette=dict(color="#0b1418", line_width=2.))
        if mode == 2:
            p.add_mesh(surface, color="#a9b8b2", **look)
        else:
            placed = (*self.pull_dir, self.placement())
            surface.cell_data["grip"] = h.grip_map(*placed) if mode == 0 else h.counted_map(*placed)
            p.add_mesh(surface, scalars="grip", cmap=GRIP_CMAP, clim=(0, 1), **look,
                       scalar_bar_args=dict(title="Grip: none to best" if mode == 0 else "Counted by one hand: none to full",
                                            n_labels=0, color="#c9d6d4", position_x=.3, position_y=.03,
                                            width=.4, height=.05, title_font_size=13))
        creases = surface.extract_feature_edges(feature_angle=40., boundary_edges=False, non_manifold_edges=False,
                                                manifold_edges=False)
        if creases.n_cells:
            p.add_mesh(creases, color="#0b1418", line_width=1.5, opacity=.7)
        extent = float(h.mesh.extents.max())
        wall = pv.Plane(center=np.array([0, 0, -.5]) @ rotation.T, direction=np.array([0, 0, 1.]) @ rotation.T,
                        i_size=extent * 1.8, j_size=extent * 1.8)
        p.add_mesh(wall, color="#9aa7a6", opacity=.18)
        if self.arrows.isChecked():
            weights = h.counted_map(*self.pull_dir, self.placement()) * mesh.area_faces
            anchor = (mesh.triangles_center * weights[:, None]).sum(axis=0) / weights.sum() \
                if weights.sum() > 0 else mesh.bounds.mean(axis=0)
            for cone in h.request.cones():
                # Beside the hold so the arrows stay visible; a pinch's two sides get one set each.
                here = anchor.copy()
                left = cone.start[0] < -.5
                here[0] = mesh.bounds[0, 0] - extent * .45 if left else mesh.bounds[1, 0] + extent * .45
                for vector, color in ((cone.start, "#4fc3f7"), (cone.end, "#ba68c8"), (cone.pull, "#ef5350")):
                    direction = grab_arrow(vector, h.request.wall_angle, *self.pull_dir)
                    p.add_mesh(pv.Arrow(start=here, direction=direction, scale=extent * .4), color=color)
            p.add_text("arrows: grip arc start (blue) · arc end (violet) · pull (red)", font_size=9, color="#c9d6d4")
        if camera is not None:
            p.camera_position = camera
        else:
            center = mesh.bounds.mean(axis=0)
            direction = np.array([.9, .55, 1.6]) / np.linalg.norm([.9, .55, 1.6])
            p.camera.focal_point = center
            p.camera.position = center + direction * extent * 3.5
            p.camera.up = (0., 1., 0.)
        p.reset_camera_clipping_range()
        p.render()

    def export(self):
        h = self.hold
        name = f"hold_{h.request.hold_type}_d{h.difficulty:.1f}_{h.request.seed}.stl"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export STL", name, "STL (*.stl)")
        if not path:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            validate_solid(h.mesh)
            report = h.report() | dict(solid_validated=True)
            h.mesh.export(path)
            with open(path[:-4] + ".json", "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(error))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Saved {path} and its JSON report", 8000)

    def closeEvent(self, event):
        self.plotter.close()
        super().closeEvent(event)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Rockform hold generator")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE + EXTRA_STYLE)
    window = GeneratorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
