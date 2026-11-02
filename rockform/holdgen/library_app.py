"""Library tab of the hold generator window: import 3D models of holds, keep holds saved from the Hold tab, and
share the library as a ZIP. The Route tab can set routes from these holds instead of generating them."""
from __future__ import annotations

from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from ..widgets import label, button
from .app import card, difficulty_color
from .library import AXES, MODEL_TYPES, UNITS, declared_units
from .shapes import HOLD_TYPES, HOLD_INFO

COLUMNS = ("Name", "Rate as", "Size (mm)", "Bolt hole", "Vertical", "Source", "Added")
UNIT_NAMES = {"mm": "Millimetres", "cm": "Centimetres", "m": "Metres", "in": "Inches"}
AXIS_NAMES = {"auto": "Find the flat back", "+Z": "+Z (Z-up, back on the print bed)", "-Z": "-Z",
              "+Y": "+Y (Y-up, lying on its back)", "-Y": "-Y", "+X": "+X", "-X": "-X"}


class LibraryView(QtWidgets.QWidget):
    def __init__(self, library, inspect=None):
        super().__init__()
        self.library = library
        self.inspect_callback = inspect

        side = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(side)
        form.setSpacing(8)
        form.addWidget(label("ROCKFORM", "eyebrow"))
        form.addWidget(label("Hold library", "brand"))
        form.addWidget(label("Holds you already have, as 3D models, and holds saved from the Hold tab. The Route "
                             "tab can set routes from them instead of generating every hold."))

        form.addWidget(label("1  Import 3D models", "section"))
        grid = QtWidgets.QGridLayout()
        self.type_pick = QtWidgets.QComboBox()
        for kind in HOLD_TYPES:
            self.type_pick.addItem(kind.capitalize(), kind)
            self.type_pick.setItemData(self.type_pick.count() - 1, HOLD_INFO[kind], QtCore.Qt.ToolTipRole)
        self.type_pick.setToolTip("How it is rated: a pinch is squeezed; every other type is hung from.")
        self.units = QtWidgets.QComboBox()
        self.units.addItem("From the file (OBJ), else mm", "")
        for key in UNITS:
            self.units.addItem(UNIT_NAMES[key], key)
        self.axis = QtWidgets.QComboBox()
        for key in AXES:
            self.axis.addItem(AXIS_NAMES[key], key)
        self.axis.setToolTip("The model's direction out of the wall. 'Find the flat back' picks the axis whose "
                             "end is the largest flat face.")
        self.turn = QtWidgets.QComboBox()
        for angle in (0, 90, 180, -90):
            self.turn.addItem(f"{angle:+d}°" if angle else "As modelled", angle)
        self.turn.setToolTip("Turn it on the wall, anticlockwise as the climber sees it, if it comes in upside down "
                             "or sideways. Routes turn it again to face the pull.")
        for row, (text, widget) in enumerate((("Rate as", self.type_pick), ("Units", self.units),
                                              ("Out of the wall", self.axis), ("Turn", self.turn))):
            grid.addWidget(label(text), row, 0)
            grid.addWidget(widget, row, 1)
        form.addLayout(grid)
        self.keep_hole = QtWidgets.QCheckBox("Keep a bolt hole it already has")
        self.keep_hole.setChecked(True)
        self.keep_hole.setToolTip("A hole through the model is found and used as is. Without one, or with this off, "
                                  "the hold is drilled for the route's bolt each time it is placed.")
        form.addWidget(self.keep_hole)
        form.addWidget(button("Import model files…", self.import_models, primary=True))
        form.addWidget(label("STL, OBJ, PLY, OFF, GLB or glTF. STL has no units: choose them. The model is "
                             "centred on its footprint with its back on the wall."))

        form.addWidget(label("2  Share", "section"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(button("Export library…", self.export_zip))
        row.addWidget(button("Add from ZIP…", self.merge_zip))
        form.addLayout(row)
        self.folder_line = label("")
        self.folder_line.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        form.addWidget(self.folder_line)
        form.addWidget(button("Open folder", self.open_folder))
        self.message = label("")
        form.addWidget(self.message)
        form.addStretch()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(360)

        frame, c = card("HOLDS (double-click a name to rename it)")
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.describe_selected)
        self.table.itemChanged.connect(self.renamed)
        c.addWidget(self.table, 1)
        self.details = label("")
        c.addWidget(self.details)
        row = QtWidgets.QHBoxLayout()
        self.inspect_button = button("Show in hold view", self.inspect)
        self.inspect_button.setToolTip("Rate it in the Hold tab, on the wall angle set there")
        row.addWidget(self.inspect_button)
        self.remove_button = button("Remove", self.remove)
        row.addWidget(self.remove_button)
        row.addStretch()
        c.addLayout(row)
        layout = QtWidgets.QHBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(frame, 1)
        self.refresh()

    # ---- table ----------------------------------------------------------------------
    def refresh(self, select=()):
        self.folder_line.setText(f"Kept in {self.library.folder}")
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.library.holds))
        for r, h in enumerate(self.library.holds):
            cells = (h.name, "", " × ".join(f"{v:.0f}" for v in h.size_mm),
                     f"Ø{h.hole_mm:g} mm, kept" if h.hole else "Drilled per route",
                     f"{h.difficulty:.1f}", h.source.capitalize(), h.added)
            for col, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if col != 0:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                if col == 0:
                    item.setData(QtCore.Qt.UserRole, h.id)
                    item.setToolTip(h.original)
                if col == 4:
                    item.setForeground(difficulty_color(h.difficulty))
                    item.setToolTip(f"Easiest pull on a vertical wall, turned to face it. Within 30° of down: "
                                    f"{h.difficulty_range[0]:.1f}–{h.difficulty_range[1]:.1f}")
                if col == 3 and not h.hole and not h.watertight:
                    item.setText("⚠ Not closed: can't drill")
                    item.setForeground(QtGui.QColor("#f0812e"))
                self.table.setItem(r, col, item)
            kind = QtWidgets.QComboBox()
            kind.addItems([t.capitalize() for t in HOLD_TYPES])
            kind.setCurrentIndex(HOLD_TYPES.index(h.type))
            kind.currentIndexChanged.connect(lambda i, hold_id=h.id: self.retype(hold_id, HOLD_TYPES[i]))
            self.table.setCellWidget(r, 1, kind)
        self.table.resizeColumnsToContents()
        self.table.clearSelection()
        model = self.table.selectionModel()
        for r, h in enumerate(self.library.holds):
            if h.id in select:
                model.select(self.table.model().index(r, 0), QtCore.QItemSelectionModel.Select
                             | QtCore.QItemSelectionModel.Rows)
        self.table.blockSignals(False)
        self.describe_selected()

    def selected_ids(self):
        return [self.table.item(i.row(), 0).data(QtCore.Qt.UserRole) for i in self.table.selectionModel().selectedRows()]

    def describe_selected(self):
        ids = self.selected_ids()
        self.inspect_button.setEnabled(len(ids) == 1 and self.inspect_callback is not None)
        self.remove_button.setEnabled(bool(ids))
        if not self.library.holds:
            self.details.setText("No holds yet. Import 3D models, or use Save to library in the Hold tab.")
            return
        if len(ids) != 1:
            self.details.setText(f"{len(self.library.holds)} holds. Select one to see its details.")
            return
        h = self.library.get(ids[0])
        notes = "".join(f"<br>⚠ {n}" for n in h.notes)
        self.details.setText(f"<b>{h.name}</b> · {h.type} · {h.triangles:,} triangles · {h.volume_cm3:.0f} cm³<br>"
                             f"From {h.original}{notes}")

    def renamed(self, item):
        if item.column() == 0:
            self.library.rename(item.data(QtCore.Qt.UserRole), item.text())

    def retype(self, hold_id, hold_type):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.library.set_type(hold_id, hold_type)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.message.setText(f"Rated as a {hold_type} now.")
        self.refresh(select=[hold_id])

    # ---- actions --------------------------------------------------------------------
    def import_models(self):
        patterns = " ".join(f"*{s}" for s in MODEL_TYPES)
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Import hold models", "", f"3D models ({patterns})")
        if not paths:
            return
        added, failed = [], []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for path in paths:
                self.message.setText(f"Importing {Path(path).name}…")
                QtWidgets.QApplication.processEvents()
                try:
                    data = Path(path).read_bytes()
                    units = self.units.currentData() or declared_units(path, data) or None
                    added.append(self.library.import_model(
                        Path(path).name, data, self.type_pick.currentData(), units=units,
                        axis=self.axis.currentData(), turn_deg=float(self.turn.currentData()),
                        hole="detect" if self.keep_hole.isChecked() else "drill"))
                except Exception as error:
                    failed.append(f"{Path(path).name}: {error}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        text = f"Added {len(added)} hold(s)." if added else ""
        notes = [f"{h.name}: {n}" for h in added for n in h.notes]
        self.message.setText("\n".join([text] + [f"⚠ {n}" for n in notes + failed]).strip())
        self.refresh(select=[h.id for h in added])

    def remove(self):
        ids = self.selected_ids()
        if not ids:
            return
        names = ", ".join(self.library.get(i).name for i in ids)
        answer = QtWidgets.QMessageBox.question(self, "Remove holds", f"Remove {names} from the library? "
                                                "Routes already on the wall keep them.")
        if answer != QtWidgets.QMessageBox.Yes:
            return
        for hold_id in ids:
            self.library.remove(hold_id)
        self.message.setText(f"Removed {len(ids)} hold(s).")
        self.refresh()

    def inspect(self):
        ids = self.selected_ids()
        if len(ids) == 1 and self.inspect_callback is not None:
            self.inspect_callback(ids[0])

    def export_zip(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export the hold library", "hold-library.zip",
                                                        "ZIP (*.zip)")
        if path:
            Path(path).write_bytes(self.library.export_zip())
            self.message.setText(f"Saved {len(self.library.holds)} hold(s) to {path}.")

    def merge_zip(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Add holds from a library ZIP", "", "ZIP (*.zip)")
        if not path:
            return
        try:
            added = self.library.merge_zip(Path(path).read_bytes())
        except Exception as error:
            self.message.setText(f"Could not add that library: {error}")
            return
        self.message.setText(f"Added {len(added)} hold(s) from {Path(path).name}.")
        self.refresh(select=[h.id for h in added])

    def open_folder(self):
        self.library.folder.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.library.folder)))
