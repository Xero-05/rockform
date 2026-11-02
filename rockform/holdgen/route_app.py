"""Route tab of the hold generator window: set a wall, plan routes of generated holds on it, see them in 3D,
move and turn holds by hand (re-rated as you go), inspect any hold in the hold view, and export STLs, reports
and a Rock Wall Builder layout."""
from __future__ import annotations

import dataclasses
import random
import numpy as np
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PySide6 import QtCore, QtGui, QtWidgets
from ..widgets import label, button, number
from .app import card, slider, difficulty_color, difficulty_word, wall_text, pull_text
from .holding import orientation
from .route import (GRADES, ROUTE_COLORS, ROLE_COLORS, Wall, face_the_pull, placement_problems, plan_route, rerate,
                    write_wall)

ROLE_NAMES = {"START": "Start", "HAND": "Hand", "FOOT": "Foot", "FINISH": "Finish"}
STYLE_NAMES = {"down": "Down-pull", "side_right": "Side-pull →", "side_left": "Side-pull ←", "undercling": "Undercling"}
COLOUR_MODES = ("Role", "Difficulty as placed", "Route")
COLUMNS = ("Route", "Role", "Type", "Difficulty", "Grab", "Turn", "Across, up (mm)", "Size (mm)")


def _hex(rgb):
    return QtGui.QColor.fromRgbF(*rgb).name()


class PlanWorker(QtCore.QThread):
    """Runs the planner off the UI thread; each progress line is forwarded."""
    line = QtCore.Signal(str)
    done = QtCore.Signal(object, object)
    failed = QtCore.Signal(str)

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs

    def run(self):
        try:
            holds, summary = plan_route(progress=self.line.emit, **self.kwargs)
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.done.emit(holds, summary)


class RouteView(QtWidgets.QWidget):
    def __init__(self, inspect=None):
        super().__init__()
        self.inspect_callback = inspect
        self.routes = []            # (name, colour, holds, summary), in the order they were set
        self.rows = []              # (route index, PlacedHold) per table row
        self.selected = None
        self.worker = None
        self.history = []           # (hold, u, v, spin, moved) before each manual adjustment, for Undo
        self.problems = {}          # id(hold) -> placement warning

        side = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(side)
        form.setSpacing(8)
        form.addWidget(label("ROCKFORM", "eyebrow"))
        form.addWidget(label("Route setter", "brand"))
        form.addWidget(label("Plans a boulder route from generated holds: each is drilled, turned so its best grip "
                             "faces the climber's pull, and rated as placed."))

        form.addWidget(label("1  Wall", "section"))
        grid = QtWidgets.QGridLayout()
        self.width_box = number(2000., 1000., 20000., 0)
        self.height_box = number(3000., 1500., 20000., 0)
        self.bolt_box = number(200., 100., 500., 0)
        for row, (text, box) in enumerate((("Width", self.width_box), ("Height", self.height_box),
                                           ("Bolt grid", self.bolt_box))):
            grid.addWidget(label(text), row, 0)
            grid.addWidget(box, row, 1)
            box.valueChanged.connect(self.wall_changed)
        form.addLayout(grid)
        row = QtWidgets.QHBoxLayout()
        self.angle = slider(-10, 60, 20, 5)
        self.angle_value = label("", "value")
        row.addWidget(self.angle, 1)
        row.addWidget(self.angle_value)
        form.addLayout(row)
        presets = QtWidgets.QHBoxLayout()
        for name, a in (("Slab", -10), ("Vertical", 0), ("20°", 20), ("30°", 30), ("45°", 45)):
            presets.addWidget(button(name, lambda _=False, a=a: self.angle.setValue(a)))
        form.addLayout(presets)
        self.angle.valueChanged.connect(self.wall_changed)

        form.addWidget(label("2  Route", "section"))
        grades = QtWidgets.QGridLayout()
        self.grade_group = QtWidgets.QButtonGroup(self)
        for i, name in enumerate(GRADES):
            b = QtWidgets.QPushButton(name)
            b.setObjectName("size")
            b.setCheckable(True)
            d, move, _ = GRADES[name]
            b.setToolTip(f"{name}: hand holds about {d:.1f} as placed, moves about {move:.0f} mm")
            self.grade_group.addButton(b, i)
            grades.addWidget(b, i // 5, i % 5)
        self.grade_group.button(3).setChecked(True)
        form.addLayout(grades)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(label("Variation"))
        self.seed = QtWidgets.QLineEdit("1")
        self.seed.setMaximumWidth(90)
        row.addWidget(self.seed)
        row.addWidget(button("Random", lambda: self.seed.setText(str(random.randint(0, 99999)))), 1)
        form.addLayout(row)
        grid = QtWidgets.QGridLayout()
        self.moves = QtWidgets.QSpinBox()
        self.moves.setRange(0, 20)
        self.moves.setSpecialValueText("To the top")
        grid.addWidget(label("Hand moves"), 0, 0)
        grid.addWidget(self.moves, 0, 1)
        self.hole = number(10.5, 0., 20., 1)
        self.hole.setSpecialValueText("No holes")
        self.hole.setToolTip("Bolt hole through hand holds (feet get Rockform's 5 mm screw). 0 = no holes.")
        grid.addWidget(label("Bolt hole"), 1, 0)
        grid.addWidget(self.hole, 1, 1)
        form.addLayout(grid)
        self.feet = QtWidgets.QCheckBox("Foot holds")
        self.feet.setChecked(True)
        form.addWidget(self.feet)

        self.add_button = button("Add route to wall", self.add_route, primary=True)
        form.addWidget(self.add_button)
        row = QtWidgets.QHBoxLayout()
        self.undo_button = button("Remove last route", self.remove_last)
        row.addWidget(self.undo_button)
        row.addWidget(button("Clear wall", self.clear_wall))
        form.addLayout(row)
        self.busy = QtWidgets.QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.hide()
        form.addWidget(self.busy)
        self.message = label("Holds already on the wall stay; new routes keep clear of them.")
        form.addWidget(self.message)
        form.addStretch()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(360)

        self.plotter = QtInteractor(self, auto_update=False)
        self.plotter.set_background("#17272e", top="#253f45")
        self.actors = {}            # hold actor in the 3D view -> PlacedHold
        self.outlines = {}          # hold actor -> its outline actor
        self.drag = None            # the hold being dragged: its actor, grab offset and preview position
        self.install_mouse()
        options = QtWidgets.QHBoxLayout()
        self.view_title = label("No routes yet: choose a grade and add a route", "section")
        self.view_title.setWordWrap(False)
        options.addWidget(self.view_title)
        options.addSpacing(16)
        options.addWidget(label("Colour by"))
        self.colour = QtWidgets.QComboBox()
        self.colour.addItems(COLOUR_MODES)
        self.colour.currentIndexChanged.connect(lambda _: self.draw())
        options.addWidget(self.colour)
        self.labels = QtWidgets.QCheckBox("Labels")
        self.labels.setChecked(True)
        self.labels.toggled.connect(lambda _: self.draw())
        options.addWidget(self.labels)
        options.addStretch()
        options.addWidget(button("Reset view", lambda: self.draw(keep_camera=False)))
        viewer = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(viewer)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(options)
        v.addWidget(self.plotter.interactor, 1)

        results = QtWidgets.QHBoxLayout()
        frame, c = card("ROUTE")
        self.route_pick = QtWidgets.QComboBox()
        self.route_pick.currentIndexChanged.connect(lambda _: self.show_summary())
        c.addWidget(self.route_pick)
        top = QtWidgets.QHBoxLayout()
        self.big = label("–", "big")
        top.addWidget(self.big)
        self.grade_line = label("", "section")
        top.addWidget(self.grade_line, 1)
        c.addLayout(top)
        self.summary = label("")
        c.addWidget(self.summary)
        c.addStretch()
        frame.setMinimumWidth(280)
        frame.setMaximumWidth(360)
        results.addWidget(frame)
        frame, c = card("HOLDS (click one to show it)")
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.row_selected)
        self.table.cellDoubleClicked.connect(lambda *_: self.inspect())
        c.addWidget(self.table, 1)
        self.hold_line = label("")
        c.addWidget(self.hold_line)
        # Manual adjustment of the selected hold: one bolt at a time, or a typed position snapped to the grid,
        # and 15 degree turns as in the Blender add-on. Every change re-rates the route.
        row = QtWidgets.QHBoxLayout()
        self.adjusters = []
        for text, tip, keys, kwargs in (
                ("←", "Move one bolt left (Ctrl+←)", "Ctrl+Left", dict(du=-1)),
                ("↑", "Move one bolt up (Ctrl+↑)", "Ctrl+Up", dict(dv=1)),
                ("↓", "Move one bolt down (Ctrl+↓)", "Ctrl+Down", dict(dv=-1)),
                ("→", "Move one bolt right (Ctrl+→)", "Ctrl+Right", dict(du=1)),
                ("↺15°", "Turn 15° anticlockwise (Ctrl+[)", "Ctrl+[", dict(turn=15.)),
                ("↻15°", "Turn 15° clockwise (Ctrl+])", "Ctrl+]", dict(turn=-15.))):
            b = button(text, lambda _=False, k=kwargs: self.adjust(**k))
            b.setToolTip(tip)
            b.setFixedWidth(34 if len(text) == 1 else 58)
            row.addWidget(b)
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
            shortcut.activated.connect(lambda k=kwargs: self.adjust(**k))
            self.adjusters.append(b)
        b = button("Face the pull", lambda: self.adjust(face=True))
        b.setToolTip("Turn the hold so its best grip faces the pull it gets on this route")
        row.addWidget(b)
        self.adjusters.append(b)
        row.addSpacing(8)
        self.u_box = number(0., -10000., 10000., 0)
        self.v_box = number(0., 0., 20000., 0)
        for box, tip in ((self.u_box, "Across the wall from its centre line"), (self.v_box, "Up from the bottom")):
            box.setToolTip(tip + "; snaps to the nearest bolt")
            box.setKeyboardTracking(False)
            box.valueChanged.connect(lambda _: self.adjust(to=(self.u_box.value(), self.v_box.value())))
            row.addWidget(box)
            self.adjusters.append(box)
        row.addStretch()
        self.undo_edit = button("Undo", self.undo)
        self.undo_edit.setToolTip("Undo the last move or turn (Ctrl+Z)")
        QtGui.QShortcut(QtGui.QKeySequence.Undo, self).activated.connect(self.undo)
        row.addWidget(self.undo_edit)
        self.inspect_button = button("Inspect in hold view", self.inspect)
        row.addWidget(self.inspect_button)
        c.addLayout(row)
        self.enable_adjusters()
        results.addWidget(frame, 2)
        frame, c = card("EXPORT")
        c.addWidget(label("Per route: drilled STLs with JSON reports, route.json and a route.svg front view. "
                          "For the wall: layout.json, which Blender's Rock Wall Builder loads with every route."))
        c.addStretch()
        self.export_button = button("Export wall…", self.export, primary=True)
        self.export_button.setEnabled(False)
        c.addWidget(self.export_button)
        frame.setMinimumWidth(220)
        frame.setMaximumWidth(280)
        results.addWidget(frame)
        bottom = QtWidgets.QWidget()
        bottom.setLayout(results)
        bottom.setMaximumHeight(340)

        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(viewer, 1)
        rl.addWidget(bottom)
        layout = QtWidgets.QHBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(right, 1)
        self.wall_changed()

    # ---- controls -------------------------------------------------------------------
    def wall(self):
        return Wall(self.width_box.value(), self.height_box.value(), float(self.angle.value()), self.bolt_box.value())

    def wall_changed(self):
        self.angle_value.setText(wall_text(self.angle.value()))
        if self.routes:
            # Positions, turns and ratings belong to the wall they were planned on.
            self.message.setText("⚠ The wall changed: the routes on it were planned and rated for the old wall. "
                                 "Clear the wall and add them again.")
        self.draw(keep_camera=bool(self.routes))

    def set_busy(self, busy):
        self.busy.setVisible(busy)
        for w in (self.add_button, self.undo_button, self.export_button):
            w.setEnabled(not busy)
        if not busy:
            self.undo_button.setEnabled(bool(self.routes))
            self.export_button.setEnabled(bool(self.routes))
        self.enable_adjusters()

    def add_route(self):
        grade = list(GRADES)[self.grade_group.checkedId()]
        avoid = [(h.u, h.v, h.radius) for *_, holds, _s in self.routes for h in holds]
        if self.routes and any(abs(s["wall_angle_deg"] - self.angle.value()) > .5 for *_, s in self.routes):
            self.message.setText("Clear the wall first: its routes were planned for another wall angle.")
            return
        self.pending = (grade, self.seed.text() or "0")
        self.worker = PlanWorker(grade=grade, wall=self.wall(), seed=self.pending[1], moves=self.moves.value() or None,
                                 avoid=avoid, foot_holds=self.feet.isChecked(), bolt=self.hole.value())
        self.worker.line.connect(lambda text: self.message.setText(text.strip()))
        self.worker.done.connect(self.route_ready)
        self.worker.failed.connect(lambda error: (self.set_busy(False),
                                                  self.message.setText(f"Could not plan this route: {error}")))
        self.set_busy(True)
        self.message.setText(f"Planning a {grade} route…")
        self.worker.start()

    def route_ready(self, holds, summary):
        grade, seed = self.pending
        name = f"{grade} seed {seed}"
        if any(r[0] == name for r in self.routes):
            name += f" ({len(self.routes) + 1})"
        color = ROUTE_COLORS[len(self.routes) % len(ROUTE_COLORS)]
        self.routes.append((name, color, holds, summary))
        self.set_busy(False)
        notes = summary.get("notes", [])
        self.message.setText(f"Added {name}: estimated {summary['estimated_grade']}, {len(holds)} holds."
                             + (f"\n{len(notes)} note(s): see the route card." if notes else ""))
        self.refresh(select_route=len(self.routes) - 1, keep_camera=len(self.routes) > 1)

    def remove_last(self):
        if self.routes:
            self.routes.pop()
            self.refresh(select_route=len(self.routes) - 1)

    def clear_wall(self):
        self.routes.clear()
        self.message.setText("Wall cleared.")
        self.refresh(select_route=-1, keep_camera=False)

    # ---- route card and hold table ---------------------------------------------------
    def refresh(self, select_route=-1, keep_camera=True, keep_selection=False):
        if not keep_selection:
            self.selected = None
        kept = {id(h) for *_, holds, _s in self.routes for h in holds}
        self.history = [step for step in self.history if id(step[0]) in kept]
        self.problems = placement_problems([holds for *_, holds, _s in self.routes], self.wall())
        self.route_pick.blockSignals(True)
        self.route_pick.clear()
        for name, color, *_ in self.routes:
            pixmap = QtGui.QPixmap(12, 12)
            pixmap.fill(QtGui.QColor(_hex(color)))
            self.route_pick.addItem(QtGui.QIcon(pixmap), name)
        self.route_pick.setCurrentIndex(select_route)
        self.route_pick.blockSignals(False)
        self.rows = [(i, h) for i, (*_, holds, _s) in enumerate(self.routes)
                     for h in sorted(holds, key=lambda h: (h.role == "FOOT", h.v))]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for r, (i, h) in enumerate(self.rows):
            name, color, *_ = self.routes[i]
            cells = (name, ROLE_NAMES[h.role], h.hold_type.capitalize(), f"{h.difficulty:.1f}",
                     STYLE_NAMES[h.style] if h.role != "FOOT" else "Foot", f"{h.spin:+.0f}°",
                     f"{h.u:.0f}, {h.v:.0f}" + (" ✎" if h.moved else "") + (" ⚠" if id(h) in self.problems else ""),
                     " × ".join(f"{v:.0f}" for v in h.size_mm))
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 0:
                    item.setForeground(QtGui.QColor(_hex(color)))
                if c == 1:
                    item.setForeground(QtGui.QColor(ROLE_COLORS[h.role]))
                if c == 3:
                    item.setForeground(difficulty_color(h.difficulty))
                if c == 6 and id(h) in self.problems:
                    item.setForeground(QtGui.QColor("#f0812e"))
                    item.setToolTip(f"This hold {self.problems[id(h)]}.")
                elif c == 6 and h.moved:
                    item.setToolTip("Moved or turned by hand")
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.clearSelection()
        rows = [r for r, (_i, h) in enumerate(self.rows) if h is self.selected]
        if rows:
            self.table.selectRow(rows[0])
        else:
            self.selected = None
        self.table.blockSignals(False)
        self.describe_selected()
        self.set_busy(False)
        self.show_summary()
        self.draw(keep_camera=keep_camera)

    def show_summary(self):
        i = self.route_pick.currentIndex()
        if not 0 <= i < len(self.routes):
            self.big.setText("–")
            self.big.setStyleSheet("")
            self.grade_line.setText("")
            self.summary.setText("")
            self.view_title.setText("No routes yet: choose a grade and add a route" if not self.routes else "")
            return
        name, color, holds, s = self.routes[i]
        self.big.setText(s["estimated_grade"])
        self.big.setStyleSheet(f"color: {_hex(color)};")
        match = "on target" if s["estimated_grade"] == s["requested_grade"] else f"asked {s['requested_grade']}"
        self.grade_line.setText(f"Estimated grade<br><span style='font-size:12px;color:#98aaa9'>{match} · "
                                f"score {s['route_score']} (target {s['requested_score']})</span>")
        moves = " → ".join(f"{c:.1f}" for c in s["move_costs"])
        notes = "".join(f"<br>⚠ {n}" for n in s.get("notes", []))
        self.summary.setText(f"{s['hand_moves']} hand moves, {sum(h.role == 'FOOT' for h in holds)} foot holds, "
                             f"rated on a {wall_text(s['wall_angle_deg']).lower()} wall.<br>Move scores: {moves}"
                             f"<br><span style='font-size:11px'>Heuristic, not calibrated on climbers.</span>{notes}")
        self.view_title.setText(f"{len(self.routes)} route(s), "
                                f"{sum(len(r[2]) for r in self.routes)} holds on a {wall_text(self.angle.value()).lower()} wall")

    def row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        i, h = self.rows[rows[0].row()]
        self.selected = h
        self.describe_selected()
        if self.route_pick.currentIndex() != i:
            self.route_pick.setCurrentIndex(i)
        self.draw()

    def describe_selected(self):
        h = self.selected
        self.enable_adjusters()
        if h is None:
            self.hold_line.setText("Select a hold to move or turn it.")
            return
        pull = "" if h.role == "FOOT" else f", expected pull: {pull_text(h.pull_deg).lower()}"
        warning = f" <span style='color:#f0812e'>⚠ It {self.problems[id(h)]}.</span>" if id(h) in self.problems else ""
        self.hold_line.setText(f"<b>{h.hold_type.capitalize()}</b> {difficulty_word(h.difficulty).lower()} "
                               f"({h.difficulty:.1f}){pull}, turned {h.spin:+.0f}°, Ø{h.hole_mm:.1f} mm hole"
                               f"{' · moved by hand' if h.moved else ''}.{warning}")
        for box, value in ((self.u_box, h.u), (self.v_box, h.v)):
            box.blockSignals(True)
            box.setSingleStep(self.bolt_box.value())      # the spin arrows step one bolt
            box.setValue(value)
            box.blockSignals(False)

    def enable_adjusters(self):
        on = self.selected is not None and not self.busy.isVisible()
        for w in self.adjusters:
            w.setEnabled(on)
        self.inspect_button.setEnabled(on and self.inspect_callback is not None)
        self.undo_edit.setEnabled(bool(self.history) and not self.busy.isVisible())

    def route_of(self, hold):
        return next(r for r in self.routes if any(h is hold for h in r[2]))

    def adjust(self, du=0, dv=0, turn=0., to=None, face=False, spin_to=None):
        """Move the selected hold by whole bolts or to a typed position (snapped to the bolt grid), or turn it,
        then re-rate its route. Holds may overlap: that is shown, not prevented."""
        h = self.selected
        if h is None or self.busy.isVisible():
            return
        wall = self.wall()
        g = wall.bolt_spacing
        u, v = wall.snap(*(to if to is not None else (h.u + du * g, h.v + dv * g)))
        spin = face_the_pull(h) if face else spin_to if spin_to is not None else (h.spin + turn + 180.) % 360. - 180.
        if (u, v, spin) == (h.u, h.v, h.spin):
            self.describe_selected()       # e.g. a typed position that snaps back to the same bolt
            return
        self.history.append((h, h.u, h.v, h.spin, h.moved))
        h.u, h.v, h.spin, h.moved = u, v, spin, True
        self.rerate_selected(h)

    def undo(self):
        if not self.history or self.busy.isVisible():
            return
        h, u, v, spin, moved = self.history.pop()
        h.u, h.v, h.spin, h.moved = u, v, spin, moved
        self.selected = h
        self.rerate_selected(h)

    def rerate_selected(self, h):
        name, _color, holds, summary = self.route_of(h)
        before = summary["estimated_grade"]
        rerate(holds, summary, self.wall())
        after = summary["estimated_grade"]
        text = (f"{name}: {h.hold_type} now difficulty {h.difficulty:.1f}; route estimate {after}"
                + (f" (was {before})." if after != before else "."))
        if h.role != "FOOT" and abs(h.spin - face_the_pull(h)) > 1:
            text += " Its best grip no longer faces the pull: Face the pull turns it back."
        self.message.setText(text)
        self.refresh(select_route=self.routes.index(self.route_of(h)), keep_selection=True)

    def inspect(self):
        if self.selected is not None and self.inspect_callback is not None:
            self.inspect_callback(self.selected)

    # ---- 3D view --------------------------------------------------------------------
    def draw(self, keep_camera=True):
        p = self.plotter
        wall = self.wall()
        camera = p.camera_position if keep_camera else None
        R = orientation(wall.angle)
        p.clear_actors()
        self.actors, self.outlines = {}, {}
        board = pv.Box(bounds=(-wall.width / 2, wall.width / 2, 0., wall.height, -18., 0.))
        board.points = board.points @ R.T
        p.add_mesh(board, color="#c9a77c", smooth_shading=False, specular=.1)
        us, vs = wall.bolt_axes()
        bolts = np.array([[u, v, .5] for u in us for v in vs]) @ R.T
        p.add_points(bolts, color="#3a2a1a", point_size=4, render_points_as_spheres=True)
        floor = pv.Plane(center=(0., -1., wall.height * .25), direction=(0., 1., 0.),
                         i_size=wall.height * 1.1, j_size=wall.width + 1000.)
        p.add_mesh(floor, color="#2d4147", opacity=.35)

        mode = self.colour.currentIndex()
        tags, tag_points = [], []
        for name, color, holds, s in self.routes:
            line = []
            for h in holds:
                mesh = h.hold.mesh.copy()
                v = mesh.vertices - [*h.hold.hole, 0.]
                v = v @ orientation(0., h.spin).T + [h.u, h.v, 0.]
                mesh.vertices = v @ R.T
                tint = (ROLE_COLORS[h.role] if mode == 0 else difficulty_color(h.difficulty).name() if mode == 1
                        else _hex(color))
                chosen, problem = h is self.selected, id(h) in self.problems
                outline = "#ffffff" if chosen else "#f0812e" if problem else "#0b1418"
                surface = pv.wrap(mesh)
                actor = p.add_mesh(surface, color=tint, smooth_shading=True, specular=.25)
                self.actors[actor] = h
                # A separate outline actor, kept so a drag can move it with the hold.
                self.outlines[actor] = p.add_silhouette(surface, color=outline,
                                                        line_width=4. if chosen or problem else 1.5)
                tip = np.array([h.u, h.v, mesh.extents[2] + 15.]) @ R.T
                if h.role != "FOOT":
                    line.append(tip)
                    first = {"START": "S ", "FINISH": "F "}.get(h.role, "")
                    tags.append(f"{first}{h.difficulty:.1f}")
                    tag_points.append(tip)
            if len(line) > 1:
                p.add_mesh(pv.lines_from_points(np.array(line)), color=_hex(color), line_width=3, opacity=.8)
        if self.labels.isChecked() and tags:
            p.add_point_labels(np.array(tag_points), tags, font_size=11, text_color="#f3f5ed", shape_color="#17272e",
                               shape_opacity=.65, show_points=False, always_visible=True)
        if mode == 0:
            p.add_text("start (green) · hand (blue) · foot (yellow) · finish (red)", font_size=9, color="#c9d6d4")
        elif mode == 1:
            p.add_text("difficulty as placed: easy (green) to hard (red)", font_size=9, color="#c9d6d4")
        # Only holds can be clicked: the route lines and labels pass right in front of them.
        holds = {id(a) for a in self.actors}
        for actor in p.renderer.actors.values():
            if id(actor) not in holds and hasattr(actor, "SetPickable"):
                actor.SetPickable(False)
        if camera is not None:
            p.camera_position = camera
        else:
            centre = np.array([0., wall.height / 2, 0.]) @ R.T
            normal = np.array([0., 0., 1.]) @ R.T
            p.camera.focal_point = centre
            p.camera.position = centre + normal * wall.height * 1.7 + np.array([wall.width * .15, -wall.height * .05, 0.])
            p.camera.up = (0., 1., 0.)
        p.reset_camera_clipping_range()
        p.render()

    def export(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Export the wall to a folder")
        if not folder:
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            layout = write_wall(self.routes, self.wall(), folder)
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(error))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.message.setText(f"Exported {len(self.routes)} route(s) to {folder}. Load {layout.name} in Blender's "
                             "Rock Wall Builder (Layouts panel) to see them on its wall.")

    # ---- dragging holds in the 3D view ------------------------------------------------
    # As in the Blender add-on: click a hold to select it, drag it to move it bolt to bolt, turn it with the
    # mouse wheel while dragging or with Q / E. Dragging anywhere else still orbits the camera.
    def install_mouse(self):
        iren = self.plotter.iren.interactor
        self.observers = {}
        for event, handler in (("LeftButtonPressEvent", self.on_press), ("MouseMoveEvent", self.on_move),
                               ("LeftButtonReleaseEvent", self.on_release),
                               ("MouseWheelForwardEvent", lambda *_: self.on_wheel(15.)),
                               ("MouseWheelBackwardEvent", lambda *_: self.on_wheel(-15.)),
                               ("CharEvent", self.on_char)):
            # Higher priority than the camera style, so a handled event can stop it orbiting or zooming.
            self.observers[event] = iren.AddObserver(event, handler, 10.)
        # pyvista binds Q to closing the view; here Q and E turn the selected hold.
        self.plotter.iren.clear_events_for_key("q")
        self.plotter.add_key_event("q", lambda: self.turn_key(15.))
        self.plotter.add_key_event("e", lambda: self.turn_key(-15.))

    def _consume(self, event):
        self.plotter.iren.interactor.GetCommand(self.observers[event]).SetAbortFlag(1)

    def _hold_at(self, x, y):
        """The hold actor under the mouse. Holds are small at full-wall zoom (and have a hole at the bolt), so
        a click that misses the mesh takes the nearest hold whose footprint circle, on screen, contains it."""
        picker = vtk.vtkPropPicker()
        picker.Pick(x, y, 0, self.plotter.renderer)
        if picker.GetActor() in self.actors:
            return picker.GetActor()
        ren = self.plotter.renderer
        R = orientation(self.angle.value())

        def screen(point):
            ren.SetWorldPoint(*(R @ point), 1.)
            ren.WorldToDisplay()
            return np.array(ren.GetDisplayPoint()[:2])

        best, nearest = None, np.inf
        for actor, h in self.actors.items():
            centre = screen([h.u, h.v, h.size_mm[2] / 2])
            reach = max(np.linalg.norm(screen([h.u + h.radius, h.v, h.size_mm[2] / 2]) - centre), 8.)
            gap = np.linalg.norm(centre - [x, y])
            if gap <= reach and gap < nearest:
                best, nearest = actor, gap
        return best

    def _wall_point(self, x, y):
        """Where the mouse ray meets the wall's surface, in wall coordinates (across, up)."""
        ren = self.plotter.renderer
        ends = []
        for depth in (0., 1.):
            ren.SetDisplayPoint(x, y, depth)
            ren.DisplayToWorld()
            w = np.array(ren.GetWorldPoint())
            ends.append(w[:3] / w[3])
        R = orientation(self.angle.value())
        normal = R @ [0., 0., 1.]
        a, b = ends
        along = float(normal @ (b - a))
        if abs(along) < 1e-9:
            return None
        hit = a + (b - a) * (-(normal @ a) / along)
        u, v, _ = R.T @ hit
        return u, v

    def _cursor(self, shape):
        self.plotter.interactor.setCursor(QtGui.QCursor(shape))

    def on_press(self, iren, _event):
        if self.busy.isVisible():
            return
        x, y = iren.GetEventPosition()
        actor = self._hold_at(x, y)
        h = self.actors.get(actor)
        if h is None:
            return                          # empty wall or background: orbit as usual
        self._consume("LeftButtonPressEvent")
        grab = self._wall_point(x, y)
        self.drag = dict(hold=h, actor=actor, offset=(h.u - grab[0], h.v - grab[1]) if grab else (0., 0.),
                         u=h.u, v=h.v, spin=h.spin, moved=False)
        self.select_hold(h)
        self._cursor(QtCore.Qt.ClosedHandCursor)

    def on_move(self, iren, _event):
        x, y = iren.GetEventPosition()
        if self.drag is None:
            if not self.busy.isVisible():
                hover = self.actors.get(self._hold_at(x, y)) is not None
                self._cursor(QtCore.Qt.OpenHandCursor if hover else QtCore.Qt.ArrowCursor)
            return
        self._consume("MouseMoveEvent")
        point = self._wall_point(x, y)
        if point is None:
            return
        d = self.drag
        u, v = self.wall().snap(point[0] + d["offset"][0], point[1] + d["offset"][1])
        if (u, v) != (d["u"], d["v"]):
            d.update(u=u, v=v, moved=True)
            self.preview_drag()

    def on_wheel(self, step):
        if self.drag is None:
            return                          # zoom as usual
        self._consume("MouseWheelForwardEvent" if step > 0 else "MouseWheelBackwardEvent")
        d = self.drag
        d.update(spin=(d["spin"] + step + 180.) % 360. - 180., moved=True)
        self.preview_drag()

    def on_char(self, iren, _event):
        # Stop VTK's own style from treating Q and E as "exit"; the key events above do the turning.
        if iren.GetKeySym() in ("q", "e", "Q", "E"):
            self._consume("CharEvent")

    def turn_key(self, step):
        if self.drag is not None:
            self.on_wheel(step)
        elif self.selected is not None:
            self.adjust(turn=step)

    def preview_drag(self):
        """Move the dragged hold's actor to the snapped bolt and turn without rebuilding the scene; the route
        is re-rated when it is dropped."""
        d, h = self.drag, self.drag["hold"]
        R = orientation(self.angle.value())
        turn = orientation(0., d["spin"] - h.spin)
        old, new = R @ [h.u, h.v, 0.], R @ [d["u"], d["v"], 0.]
        m = np.eye(4)
        m[:3, :3] = R @ turn @ R.T
        m[:3, 3] = new - m[:3, :3] @ old
        matrix = vtk.vtkMatrix4x4()
        for i in range(4):
            for j in range(4):
                matrix.SetElement(i, j, m[i, j])
        d["actor"].SetUserMatrix(matrix)
        self.outlines[d["actor"]].SetUserMatrix(matrix)
        # The drop target is checked against every other hold, as after any move.
        others = [[o for o in holds if o is not h] for *_, holds, _s in self.routes]
        probe = dataclasses.replace(h, u=d["u"], v=d["v"])
        clash = placement_problems(others + [[probe]], self.wall()).get(id(probe))
        self.hold_line.setText(f"Moving <b>{h.hold_type}</b> to {d['u']:.0f}, {d['v']:.0f}, turned {d['spin']:+.0f}°"
                               + (f" <span style='color:#f0812e'>⚠ it {clash}</span>" if clash else "")
                               + " · release to place, wheel or Q / E to turn")
        self.plotter.render()

    def on_release(self, _iren, _event):
        d, self.drag = self.drag, None
        if d is None:
            return
        self._consume("LeftButtonReleaseEvent")
        self._cursor(QtCore.Qt.OpenHandCursor)
        if d["moved"]:
            self.adjust(to=(d["u"], d["v"]), spin_to=d["spin"])     # one undo step; re-rates and redraws
        else:
            self.draw()                                                # a click: show the selection outline

    def select_hold(self, h):
        """Select a hold from the 3D view without rebuilding the scene (it may be about to be dragged)."""
        self.selected = h
        rows = [r for r, (_i, o) in enumerate(self.rows) if o is h]
        self.table.blockSignals(True)
        if rows:
            self.table.selectRow(rows[0])
            self.table.scrollToItem(self.table.item(rows[0], 0))
        self.table.blockSignals(False)
        i = self.routes.index(self.route_of(h))
        if self.route_pick.currentIndex() != i:
            self.route_pick.setCurrentIndex(i)
        self.describe_selected()

    def close(self):
        self.plotter.close()
