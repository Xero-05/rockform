from __future__ import annotations

import os
os.environ.setdefault("QT_API", "pyside6")
from copy import deepcopy
from pathlib import Path
import sys
import uuid
import numpy as np
import trimesh
import pyvista as pv
from pyvistaqt import QtInteractor
from PySide6 import QtCore, QtGui, QtWidgets
from scipy.spatial import cKDTree
from .model import Project, Revision, GeometryError, Mount
from .geometry import demo_scan, extract_hold, prepare_hold, build_solid, oriented_patch, frame_for_patch, generated_faces
from .cleanup import CleanupOptions, clean_revision, gap_info
from .detection import suggest_holds
from .grip import assess_grip, review_text
from .features import prepare_feature
from .mounts import generate_mounts, apply_mounts, validate_mounts
from .storage import import_scan, save_project, load_project, export_hold
from .widgets import STYLE, label, button, number, ProfileDialog, Worker
from . import __version__


def polydata(mesh):
    return pv.PolyData(mesh.vertices, np.column_stack([np.full(len(mesh.faces), 3), mesh.faces]).ravel())


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Rockform {__version__} · Scan to hold")
        self.resize(1480, 930)
        self.setMinimumSize(1120, 720)
        self.project = None
        self.project_path = None
        self.preview = None
        self.hold_preview = None
        self.selected = np.zeros(0, dtype=bool)
        self.cleanup_area = np.zeros(0, dtype=bool)
        self.candidates = []
        self.hold_index = -1
        self.worker = None
        self.thread = None
        self.dirty = False
        self.updating = False
        self._brush_down = False
        self._brush_snapshot = None
        self._last_paint = 0
        self._make_ui()
        self._make_menu()
        for name in self._saved_controls:
            widget = getattr(self, name)
            signal = (widget.toggled if isinstance(widget, QtWidgets.QCheckBox) else
                      widget.currentTextChanged if isinstance(widget, QtWidgets.QComboBox) else widget.valueChanged)
            signal.connect(self.workspace_changed)
        self.gap_list.itemChanged.connect(self.workspace_changed)
        self.update_profile_summary()
        self.refresh(reset_camera=True)

    _saved_controls = ("repair_check", "cleanup_selected_only", "fragment", "smoothing", "reduction",
                       "auto_fill_gaps", "gap_limit", "feature_min", "feature_max", "sensitivity",
                       "prepare_boundary", "backing", "edge_flare", "hold_scale", "tilt_x", "tilt_y",
                       "mount_mode", "anti_rotation", "screw_count", "mount_x", "mount_y", "drag_mounts",
                       "cutaway", "cut_position", "brush_mode", "brush_radius", "compare")

    def workspace_changed(self, *_):
        if self.project is not None and not self.worker:
            self.dirty = True

    def capture_workspace(self):
        """Save unaccepted work without silently applying it or keeping a hold."""
        values = {}
        for name in self._saved_controls:
            widget = getattr(self, name)
            values[name] = (widget.isChecked() if isinstance(widget, QtWidgets.QCheckBox) else
                            widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else widget.value())
        self.project.workspace = dict(revision_id=self.project.current.id,
            selection=np.flatnonzero(self.selected).tolist(), cleanup_area=np.flatnonzero(self.cleanup_area).tolist(),
            hold_id=self.hold.id if self.hold else None, tab=self.tabs.currentIndex(),
            view=self.view_mode.currentText(), controls=values,
            camera=[list(point) for point in self.plotter.camera_position],
            parallel_projection=bool(self.plotter.camera.parallel_projection),
            parallel_scale=float(self.plotter.camera.parallel_scale), mount_row=self.mount_list.currentRow(),
            inspected_gaps=bool(self.gap_list.count()),
            checked_gaps=[self.gap_list.item(i).data(QtCore.Qt.UserRole) for i in range(self.gap_list.count())
                          if self.gap_list.item(i).checkState() == QtCore.Qt.Checked])
        self.project.pending_hold = self.hold_preview
        self.project.pending_cleanup = self.preview

    def restore_workspace(self):
        state = self.project.workspace
        index = next((i for i, hold in enumerate(self.project.holds) if hold.id == state.get("hold_id")),
                     0 if not state and self.project.holds else -1)
        if index >= 0:
            self.hold_list.setCurrentRow(index)
            self.select_hold(index)
        if state.get("revision_id") == self.project.current.id:
            if state.get("inspected_gaps"):
                self.inspect_gaps()
                for i in range(self.gap_list.count()):
                    item = self.gap_list.item(i)
                    if item.data(QtCore.Qt.UserRole) in state.get("checked_gaps", []):
                        item.setCheckState(QtCore.Qt.Checked)
            for key, target in (("selection", self.selected), ("cleanup_area", self.cleanup_area)):
                ids = np.asarray(state.get(key, []), dtype=int)
                if np.any((ids < 0) | (ids >= len(target))):
                    raise GeometryError("Saved selection does not match its scan revision.")
                target[ids] = True
            self.hold_preview = self.project.pending_hold
            self.preview = self.project.pending_cleanup
            if self.hold_preview:
                self.shape_fields(self.hold_preview)
                self.hold_preview_text.setText(self.hold_preview.preparation + "\n" + review_text(self.hold_preview)
                                              + "\nRestored preview — Keep hold when ready.")
            if self.preview:
                self.preview_text.setText(self.preview.summary + "\n" + "\n".join(self.preview.warnings))
        for name in self._saved_controls:
            if name not in state.get("controls", {}):
                continue
            widget, value = getattr(self, name), state["controls"][name]
            blocker = QtCore.QSignalBlocker(widget)
            if isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, QtWidgets.QComboBox):
                widget.setCurrentText(value)
            else:
                widget.setValue(value)
            del blocker
        self.update_profile_summary()
        self.mount_list.setCurrentRow(state.get("mount_row", -1))
        # Restore pending coordinate edits after selecting the stored mount row.
        for name in ("mount_x", "mount_y"):
            if name in state.get("controls", {}):
                getattr(self, name).setValue(state["controls"][name])
        default_view = "Hold" if index >= 0 else "Working scan"
        view = state.get("view", default_view)
        if (view == "Hold" and self.hold is None or view == "Hold preview" and self.hold_preview is None
                or view == "Cleanup preview" and self.preview is None):
            view = default_view
        self.view_mode.setCurrentText(view)
        self.tabs.setCurrentIndex(state.get("tab", 1 if index >= 0 else 0))
        self.refresh(reset_camera=True)
        if "camera" in state:
            camera = np.asarray(state["camera"], dtype=float)
            if camera.shape == (3, 3) and np.isfinite(camera).all():
                self.plotter.camera_position = camera.tolist()
                self.plotter.camera.parallel_projection = state.get("parallel_projection", False)
                self.plotter.camera.parallel_scale = state.get("parallel_scale", 1.)
                self.plotter.render()

    @property
    def hold(self):
        if self.project and 0 <= self.hold_index < len(self.project.holds):
            return self.project.holds[self.hold_index]
        return None

    def _make_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 12)
        heading = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QVBoxLayout()
        brand.addWidget(label("ROCKFORM", "brand"))
        subtitle = label("FROM NATURAL SURFACE TO PRINTABLE FORM", "eyebrow")
        subtitle.setMinimumWidth(405)
        brand.addWidget(subtitle)
        heading.addLayout(brand)
        heading.addStretch()
        self.top_controls = QtWidgets.QWidget()
        actions = QtWidgets.QHBoxLayout(self.top_controls)
        self.import_button = button("Import scan", self.import_dialog)
        actions.addWidget(self.import_button)
        actions.addWidget(button("Open project", self.open_dialog))
        self.save_button = button("Save project", self.save_dialog)
        actions.addWidget(self.save_button)
        self.export_button = button("Export STL", self.export_dialog, True)
        actions.addWidget(self.export_button)
        actions.addWidget(button("Guide", self.show_guide))
        heading.addWidget(self.top_controls)
        layout.addLayout(heading)
        self.message_panel = QtWidgets.QWidget()
        message_row = QtWidgets.QHBoxLayout(self.message_panel)
        message_row.setContentsMargins(8, 4, 8, 4)
        self.message_text = label("")
        self.message_text.setStyleSheet("color: #f1c897;")
        message_row.addWidget(self.message_text, 1)
        message_row.addWidget(button("Go to repair", lambda: self.tabs.setCurrentIndex(0)))
        message_row.addWidget(button("Dismiss", self.message_panel.hide))
        layout.addWidget(self.message_panel)
        self.message_panel.hide()
        layout.addSpacing(12)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(split, 1)
        self.sidebar = QtWidgets.QWidget()
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(260)
        side = QtWidgets.QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 5, 12, 0)
        side.addWidget(label("01  /  SCAN", "eyebrow"))
        self.scan_info = label("No scan yet. Import an OBJ or STL from your iPhone scanner.")
        side.addWidget(self.scan_info)
        self.scan_button = button("View working scan", self.show_scan)
        side.addWidget(self.scan_button)
        history = QtWidgets.QHBoxLayout()
        self.undo_button = button("Undo", self.undo)
        self.redo_button = button("Redo", self.redo)
        history.addWidget(self.undo_button)
        history.addWidget(self.redo_button)
        side.addLayout(history)
        side.addWidget(button("Reset scan", self.reset_scan))
        side.addSpacing(20)
        side.addWidget(label("02  /  HOLD LIBRARY", "eyebrow"))
        self.hold_list = QtWidgets.QListWidget()
        self.hold_list.currentRowChanged.connect(self.select_hold)
        self.hold_list.itemDoubleClicked.connect(self.rename_hold)
        side.addWidget(self.hold_list, 1)
        side.addWidget(button("Remove selected hold", self.remove_hold))
        side.addWidget(label("SAMPLE SCANS", "eyebrow"))
        self.demo_choice = QtWidgets.QComboBox()
        self.demo_choice.addItems(["Ledge", "Rounded", "Pocket"])
        side.addWidget(self.demo_choice)
        side.addWidget(button("Open synthetic sample", self.open_demo))
        side.addWidget(label("Shape prototypes only. Physical qualification is a separate step."))
        split.addWidget(self.sidebar)

        center = QtWidgets.QWidget()
        viewing = QtWidgets.QVBoxLayout(center)
        viewing.setContentsMargins(8, 0, 8, 0)
        toolbar = QtWidgets.QHBoxLayout()
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(["Working scan", "Original scan", "Cleanup preview", "Hold", "Hold preview"])
        self.view_mode.currentTextChanged.connect(lambda _: self.refresh())
        toolbar.addWidget(self.view_mode)
        toolbar.addStretch()
        toolbar.addWidget(button("Fit", lambda: self.plotter.reset_camera()))
        toolbar.addWidget(button("Face scan", self.face_scan))
        self.compare = QtWidgets.QCheckBox("Compare original")
        self.compare.toggled.connect(lambda _: self.refresh())
        toolbar.addWidget(self.compare)
        viewing.addLayout(toolbar)
        self.workflow_help = label("1 Import and check dimensions → 2 Preview repair, then Apply → 3 Suggest / select → Preview hold → Keep hold", "muted")
        viewing.addWidget(self.workflow_help)
        self.plotter = QtInteractor(center, auto_update=False)
        self.plotter.set_background("#17272e", top="#253f45")
        self.plotter.enable_anti_aliasing("msaa")
        self.plotter.add_axes(color="#a4b7b9")
        viewing.addWidget(self.plotter.interactor, 1)
        self.viewport_info = label("Drag to orbit · Scroll to zoom · Right-drag to paint when a brush is selected")
        viewing.addWidget(self.viewport_info)
        brushbar = QtWidgets.QHBoxLayout()
        self.brush_mode = QtWidgets.QComboBox()
        self.brush_mode.addItems(["Navigate", "Select hold", "Erase hold", "Protect grip", "Unprotect", "Cleanup area", "Erase cleanup area"])
        brushbar.addWidget(self.brush_mode, 1)
        self.brush_radius = number(25, 1, 250, 1)
        brushbar.addWidget(label("Brush radius"))
        brushbar.addWidget(self.brush_radius)
        brushbar.addWidget(button("Clear selection", self.clear_selection))
        viewing.addLayout(brushbar)
        self.selection_info = label("Teal: protected grip · Amber: hold selection · Blue: cleanup area")
        viewing.addWidget(self.selection_info)
        split.addWidget(center)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMinimumWidth(330)
        self.tabs.setMaximumWidth(410)
        self._clean_tab()
        self._shape_tab()
        self._mount_tab()
        split.addWidget(self.tabs)
        split.setSizes([215, 815, 365])
        footer = QtWidgets.QHBoxLayout()
        self.activity = label("Ready · Files stay on this computer")
        footer.addWidget(self.activity, 1)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximumWidth(250)
        self.progress.hide()
        footer.addWidget(self.progress)
        self.cancel_button = button("Cancel", self.cancel_job)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self.statusBar().showMessage("Local workspace · millimeters")
        # Consume painting at the Qt boundary; VTK styles can swallow release events.
        self.plotter.interactor.installEventFilter(self)

    def _scroll_tab(self, title):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(contents)
        layout.setContentsMargins(14, 16, 10, 12)
        layout.setSpacing(10)
        scroll.setWidget(contents)
        self.tabs.addTab(scroll, title)
        return layout

    def _clean_tab(self):
        layout = self._scroll_tab("1 Repair")
        layout.addWidget(label("Repair the scan first", "section"))
        layout.addWidget(label("Preview repair, inspect the highlighted changes, then Apply. Your original scan is always retained."))
        layout.addWidget(button("Preview repair & cleanup", self.preview_cleanup, True))
        self.preview_text = label("No pending cleanup")
        layout.addWidget(self.preview_text)
        row = QtWidgets.QHBoxLayout()
        self.accept_button = button("Apply preview", self.accept_cleanup)
        self.reject_button = button("Discard", self.discard_cleanup)
        row.addWidget(self.accept_button)
        row.addWidget(self.reject_button)
        layout.addLayout(row)
        layout.addWidget(label("Repair settings", "section"))
        self.repair_check = QtWidgets.QCheckBox("Join seams and repair surface connections")
        self.repair_check.setChecked(True)
        layout.addWidget(self.repair_check)
        self.cleanup_selected_only = QtWidgets.QCheckBox("Only clean the blue brush region")
        layout.addWidget(self.cleanup_selected_only)
        form = QtWidgets.QFormLayout()
        self.fragment = number(0.2, 0, 20, 2, " %")
        self.smoothing = QtWidgets.QSpinBox()
        self.smoothing.setRange(0, 50)
        self.reduction = number(0, 0, 85, 0, " %")
        form.addRow("Remove fragments below", self.fragment)
        form.addRow("Smoothing passes", self.smoothing)
        form.addRow("Reduce triangle count", self.reduction)
        layout.addLayout(form)
        layout.addWidget(label("Fragment size is a percentage of the largest connected surface area. Smoothing retains boundaries and marked sharp edges."))
        layout.addWidget(label("Missing scan patches", "section"))
        self.auto_fill_gaps = QtWidgets.QCheckBox("Automatically repair small interior gaps")
        self.auto_fill_gaps.setChecked(True)
        layout.addWidget(self.auto_fill_gaps)
        self.gap_list = QtWidgets.QListWidget()
        self.gap_list.setMaximumHeight(130)
        self.gap_list.currentRowChanged.connect(lambda _: self.refresh())
        layout.addWidget(self.gap_list)
        self.gap_limit = number(10, 0.5, 100)
        f = QtWidgets.QFormLayout()
        f.addRow("Maximum repair diameter", self.gap_limit)
        layout.addLayout(f)
        layout.addWidget(button("Inspect boundaries", self.inspect_gaps))
        layout.addWidget(label("New patches appear yellow; removed pieces appear coral. The outer scan edge stays open. Large or tangled gaps stay visible for review."))
        layout.addStretch()

    def _shape_tab(self):
        layout = self._scroll_tab("2 Holds")
        layout.addWidget(label("Discover a grip feature", "section"))
        form = QtWidgets.QFormLayout()
        self.feature_min = number(50, 5, 500)
        self.feature_max = number(300, 10, 1000)
        self.sensitivity = number(50, 0, 100, 0, " %")
        self.up_axis = QtWidgets.QComboBox()
        self.up_axis.addItems(["+Y", "+Z", "+X", "−Y", "−Z", "−X"])
        self.up_axis.currentIndexChanged.connect(self.set_up_axis)
        form.addRow("Smallest feature", self.feature_min)
        form.addRow("Largest feature", self.feature_max)
        form.addRow("Sensitivity", self.sensitivity)
        form.addRow("Scan upward direction", self.up_axis)
        layout.addLayout(form)
        layout.addWidget(button("Suggest holds", self.suggest, True))
        self.candidate_list = QtWidgets.QListWidget()
        self.candidate_list.setMaximumHeight(150)
        self.candidate_list.currentRowChanged.connect(self.choose_candidate)
        layout.addWidget(self.candidate_list)
        layout.addWidget(label("Choose a suggestion, or set the brush to Select hold and RIGHT-drag over the scan. Suggestions need broad natural relief and a contact slope away from the cut edge. No suitable features is a valid result."))
        self.prepare_boundary = QtWidgets.QCheckBox("Isolate natural feature")
        self.prepare_boundary.setChecked(True)
        self.prepare_boundary.setToolTip("Use the painted area to identify a protrusion or recess, then trace its root at the surrounding wall. Protrusions get no added plate. Turn off only to build a backed surface patch from the painted outline.")
        layout.addWidget(self.prepare_boundary)
        layout.addWidget(button("Preview hold from selection", self.create_hold, True))
        self.hold_preview_text = label("Preview a shape, then Keep hold to add it to your library.")
        layout.addWidget(self.hold_preview_text)
        preview_row = QtWidgets.QHBoxLayout()
        self.keep_hold_button = button("Keep hold", self.accept_hold_preview, True)
        self.discard_hold_button = button("Discard", self.discard_hold_preview)
        preview_row.addWidget(self.keep_hold_button)
        preview_row.addWidget(self.discard_hold_button)
        layout.addLayout(preview_row)
        layout.addWidget(label("Flat-backed shape", "section"))
        form = QtWidgets.QFormLayout()
        self.backing = number(0, 0, 100)
        self.edge_flare = number(0, 0, 100)
        self.edge_flare.setToolTip("Minimum outward width. New shapes expand this as needed for a gradual wall return; the actual width is shown in the review. Legacy shapes retain their old taper until upgraded.")
        self.hold_scale = number(100, 5, 500, 1, " %")
        self.tilt_x = number(0, -70, 70, 1, "°")
        self.tilt_y = number(0, -70, 70, 1, "°")
        form.addRow("Added backing", self.backing)
        form.addRow("Minimum wall return", self.edge_flare)
        form.addRow("Surface scale", self.hold_scale)
        form.addRow("Back tilt X", self.tilt_x)
        form.addRow("Back tilt Y", self.tilt_y)
        layout.addLayout(form)
        layout.addWidget(button("Rebuild preview / selected hold", self.rebuild_hold))
        layout.addWidget(button("Re-extract natural feature", self.use_tapered_defaults))
        layout.addWidget(label("Raised features close at their natural root with 0 mm added backing and no surrounding plate. Recesses need material beneath and around the cavity. Green is scanned rock; amber is added material. Re-extract natural feature creates a new preview and keeps the old design intact."))
        layout.addStretch()

    def _mount_tab(self):
        layout = self._scroll_tab("3 Mounts")
        layout.addWidget(label("Fit hardware to the shape", "section"))
        self.mount_mode = QtWidgets.QComboBox()
        self.mount_mode.addItems(["bolt-on", "screw-on"])
        layout.addWidget(self.mount_mode)
        self.anti_rotation = QtWidgets.QCheckBox("Add anti-rotation screw (bolt-on)")
        self.anti_rotation.setChecked(False)
        layout.addWidget(self.anti_rotation)
        self.screw_count = QtWidgets.QSpinBox()
        self.screw_count.setRange(2, 10)
        self.screw_count.setValue(2)
        f = QtWidgets.QFormLayout()
        f.addRow("Screw-on hole count", self.screw_count)
        layout.addLayout(f)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(button("Bolt profile…", lambda: self.edit_profile("bolt")))
        row.addWidget(button("Screw profile…", lambda: self.edit_profile("screw")))
        layout.addLayout(row)
        layout.addWidget(label("Ready-to-use prototype profiles are included. Match them to your hardware before printing. Profile changes affect new mounts; existing holes keep their own dimensions."))
        self.profile_summary = label("")
        layout.addWidget(self.profile_summary)
        self.mount_mode.currentTextChanged.connect(self.update_profile_summary)
        self.anti_rotation.toggled.connect(self.update_profile_summary)
        layout.addWidget(button("Generate / regenerate mounts", self.auto_mounts, True))
        self.mount_list = QtWidgets.QListWidget()
        self.mount_list.setMaximumHeight(130)
        self.mount_list.currentRowChanged.connect(self.select_mount)
        layout.addWidget(self.mount_list)
        form = QtWidgets.QFormLayout()
        self.mount_x = number(0, -10000, 10000)
        self.mount_y = number(0, -10000, 10000)
        form.addRow("Hole X", self.mount_x)
        form.addRow("Hole Y", self.mount_y)
        layout.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(button("Set position", self.set_mount_position))
        row.addWidget(button("Hole dimensions…", self.edit_mount_profile))
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(button("Add screw", self.add_mount))
        row.addWidget(button("Delete hole", self.delete_mount))
        layout.addLayout(row)
        self.mount_status = label("Select a hold to generate mounting geometry.")
        layout.addWidget(self.mount_status)
        self.drag_mounts = QtWidgets.QCheckBox("Drag mount handles in the 3D view")
        self.drag_mounts.setChecked(True)
        self.drag_mounts.toggled.connect(lambda _: self.refresh())
        layout.addWidget(self.drag_mounts)
        self.cutaway = QtWidgets.QCheckBox("Cutaway preview")
        self.cutaway.toggled.connect(lambda _: self.refresh())
        layout.addWidget(self.cutaway)
        self.cut_position = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.cut_position.setRange(0, 100)
        self.cut_position.setValue(50)
        self.cut_position.valueChanged.connect(lambda _: self.refresh())
        layout.addWidget(self.cut_position)
        layout.addWidget(button("Preview drilled hold", self.drill_preview, True))
        layout.addWidget(label("Preview cuts before export. Orange handles are editable; invalid layouts remain visible with an explanation."))
        layout.addStretch()

    def _make_menu(self):
        for text, shortcut, callback in [("Import scan", "Ctrl+I", self.import_dialog),
                                         ("Open project", "Ctrl+O", self.open_dialog),
                                         ("Save project", "Ctrl+S", self.save_dialog),
                                         ("Undo scan edit", "Ctrl+Z", self.undo),
                                         ("Redo scan edit", "Ctrl+Y", self.redo)]:
            action = QtGui.QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda checked=False, fn=callback: fn() if not self.worker else None)
            self.addAction(action)

    def run_job(self, title, operation, success):
        if self.worker:
            return
        self.activity.setText(title)
        self.message_panel.hide()
        self.progress.show()
        self.progress.setValue(0)
        self.cancel_button.show()
        for widget in (self.sidebar, self.tabs, self.top_controls):
            widget.setEnabled(False)
        self.worker = Worker(operation)
        self._job_callback = success
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.job_progress)
        # Route through QObject slots with main-thread affinity. Connecting a Python
        # closure directly can execute UI/VTK work on the geometry worker thread.
        self.worker.success.connect(self.job_success, QtCore.Qt.QueuedConnection)
        self.worker.failure.connect(self.job_failure, QtCore.Qt.QueuedConnection)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.job_finished)
        self.thread.start()

    @QtCore.Slot(object)
    def job_success(self, result):
        try:
            self._job_callback(result)
        except Exception as error:
            self.show_error(str(error))

    @QtCore.Slot(str)
    def job_failure(self, message):
        self.show_error(message)

    @QtCore.Slot(int, str)
    def job_progress(self, value, text):
        self.progress.setValue(value)
        self.activity.setText(text)

    @QtCore.Slot()
    def job_finished(self):
        self.worker = None
        self.thread.deleteLater()
        self.thread = None
        self.progress.hide()
        self.cancel_button.hide()
        for widget in (self.sidebar, self.tabs, self.top_controls):
            widget.setEnabled(True)
        self.refresh()

    def cancel_job(self):
        if self.worker:
            self.worker.context.cancelled = True
            self.activity.setText("Cancelling at the next processing checkpoint…")

    @QtCore.Slot(str)
    def show_error(self, message):
        self.activity.setText(message)
        if "cancelled" not in message.lower():
            self.message_text.setText(message)
            self.message_panel.show()

    def show_guide(self):
        QtWidgets.QMessageBox.information(self, "Rockform quick start",
            "1. Import the OBJ/STL. Check the dimensions. Scaniverse's declared meter units are now detected automatically.\n\n"
            "2. In 1 Repair, click Preview repair & cleanup. Inspect yellow filled gaps and coral removed fragments. Click Apply preview.\n\n"
            "3. In 2 Holds, click Suggest holds and choose a result. Or select the Select hold brush below the viewer and RIGHT-drag over a small feature.\n\n"
            "4. Click Preview hold from selection. Edge preparation can trim a troublesome border while preserving the remaining surface. Inspect the result, then click Keep hold.\n\n"
            "5. Keep Isolate natural feature checked. Protrusions close at their root without an added plate; recesses receive material under the cavity. Green shows rock and amber shows added support. Re-extract natural feature creates a replacement preview while keeping the old design.\n\n"
            "6. In 3 Mounts, the prototype bolt and screw profiles are ready to try. Their size requirements are shown below the profile buttons. Match actual hardware before printing. Generate mounts, preview the holes, and export STL.\n\n"
            "7. Save project preserves kept holds, unfinished previews, painted selections and your view. Reopen the .rockform file with Open project to continue. Keep hold and Apply preview still require your acceptance.\n\n"
            "Left-drag rotates; the wheel zooms. Face scan looks straight at the surface. A wall scan is allowed to have an open outer edge; only the extracted hold must be closed.")

    def face_scan(self):
        if not self.project:
            return
        self.show_scan()
        mesh = self.project.current.mesh
        frame = frame_for_patch(mesh)
        normal, up = frame[2, :3], frame[1, :3]
        center = mesh.vertices.mean(axis=0)
        self.plotter.camera_position = [center + normal * max(mesh.extents.max() * 2, 1), center, up]
        self.plotter.reset_camera()

    def confirm_replace(self):
        if not self.dirty:
            return True
        return QtWidgets.QMessageBox.question(self, "Unsaved project", "Replace the current project and discard unsaved changes?",
                  QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel, QtWidgets.QMessageBox.Cancel) == QtWidgets.QMessageBox.Discard

    def set_project(self, project, path=None):
        self.project = project
        self.update_profile_summary()
        self.project_path = path
        self.hold_index = -1
        self.preview = None
        self.hold_preview = None
        self.message_panel.hide()
        self.dirty = path is None
        self.reset_selection()
        self.update_hold_list()
        axis = int(np.argmax(np.abs(project.up)))
        up_index = {1: 0, 2: 1, 0: 2}[axis] + (3 if project.up[axis] < 0 else 0)
        self.up_axis.blockSignals(True)
        self.up_axis.setCurrentIndex(up_index)
        self.up_axis.blockSignals(False)
        self.view_mode.setCurrentText("Working scan")
        self.activity.setText("Scan loaded · Confirmed units: millimeters")
        self.tabs.setCurrentIndex(0)
        self.refresh(reset_camera=True)
        self.restore_workspace()
        if path:
            self.dirty = False
            self.activity.setText(f"Project restored · {len(project.holds)} kept holds" +
                                  (" · unfinished preview restored" if self.hold_preview or self.preview else ""))

    def reset_selection(self):
        self.selected = np.zeros(len(self.project.current.mesh.faces), dtype=bool)
        self.cleanup_area = np.zeros(len(self.project.current.mesh.vertices), dtype=bool)
        self.gap_list.clear()
        self.candidate_list.clear()
        self.candidates = []
        self.preview = None
        self.hold_preview = None
        self.hold_preview_text.setText("Preview a shape, then Keep hold to add it to your library.")

    def open_demo(self):
        if self.worker or not self.confirm_replace():
            return
        kind = self.demo_choice.currentText().lower()
        self.set_project(Project(demo_scan(kind), f"Synthetic {kind} — not a phone scan"))

    def import_dialog(self):
        if self.worker or not self.confirm_replace():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import a phone mesh", "", "Triangle meshes (*.obj *.stl)")
        if not path:
            return
        self.run_job("Reading scan…", lambda ctx: import_scan(path, context=ctx), self.confirm_scale)

    def confirm_scale(self, project):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Confirm scan scale")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(label("Set real-world dimensions", "section"))
        extent = project.original.extents / project.scale_factor
        layout.addWidget(label(f"File bounds: {extent[0]:.3f} × {extent[1]:.3f} × {extent[2]:.3f} file units."))
        units = QtWidgets.QComboBox()
        units.addItems(["Millimeters", "Meters", "Centimeters", "Inches", "Measured reference"])
        if project.source_units:
            units.setCurrentText(project.source_units)
            layout.addWidget(label(f"Detected from file: {project.source_units}. Confirm the real-world dimensions below."))
        form = QtWidgets.QFormLayout()
        form.addRow("File units / calibration", units)
        file_distance = number(1, 0.000001, 1e8, 5, " file units")
        measured = number(100, 0.000001, 1e8, 3)
        form.addRow("Reference distance in scan", file_distance)
        form.addRow("Measured reference distance", measured)
        layout.addLayout(form)
        layout.addWidget(label("Use a distance measured in the scanner and the same physical reference. Check the resulting dimensions below."))
        dims = label("")
        layout.addWidget(dims)
        def factor():
            return [1, 1000, 10, 25.4][units.currentIndex()] if units.currentIndex() < 4 else measured.value() / file_distance.value()
        def update():
            dims.setText("Result: " + " × ".join(f"{x:.1f}" for x in extent * factor()) + " mm")
        units.currentIndexChanged.connect(update)
        measured.valueChanged.connect(update)
        file_distance.valueChanged.connect(update)
        update()
        controls = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            multiplier = factor()
            ratio = multiplier / project.scale_factor
            project.original.vertices *= ratio
            project.revisions[0].mesh.vertices *= ratio
            project.scale_factor = multiplier
            self.set_project(project)

    def open_dialog(self):
        if self.worker or not self.confirm_replace():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Rockform project", "", "Rockform project (*.rockform)")
        if path:
            self.run_job("Opening project…", lambda ctx: load_project(path, ctx), lambda p: self.set_project(p, path))

    def save_dialog(self):
        if not self.project or self.worker:
            return
        path = self.project_path
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save editable project", "My rock scan.rockform", "Rockform project (*.rockform)")
        if not path:
            return
        if not path.lower().endswith(".rockform"):
            path += ".rockform"
        def saved(result):
            self.project_path = result
            self.dirty = False
            self.activity.setText("Project saved · holds, previews, selections and current view")
        self.capture_workspace()
        self.run_job("Saving project…", lambda ctx: save_project(self.project, path, ctx), saved)

    def show_scan(self):
        self.view_mode.setCurrentText("Working scan")

    def undo(self):
        if self.project and not self.worker:
            self.project.undo()
            self.reset_selection()
            self.dirty = True
            self.refresh()

    def redo(self):
        if self.project and not self.worker:
            self.project.redo()
            self.reset_selection()
            self.dirty = True
            self.refresh()

    def reset_scan(self):
        if self.project and not self.worker:
            self.project.reset()
            self.reset_selection()
            self.dirty = True
            self.show_scan()
            self.refresh()

    def inspect_gaps(self):
        if not self.project:
            return
        try:
            self.gaps = gap_info(self.project.current.mesh)
            self.gap_list.clear()
            for gap in self.gaps:
                suffix = "outer boundary" if gap["outer"] else "interior gap"
                item = QtWidgets.QListWidgetItem(f"{gap['diameter']:.1f} mm · {suffix}")
                item.setData(QtCore.Qt.UserRole, gap["id"])
                if not gap["outer"]:
                    item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                    item.setCheckState(QtCore.Qt.Unchecked)
                self.gap_list.addItem(item)
            if not self.gaps:
                self.activity.setText("No simple gap loops found. Preview repair to separate any touching boundaries first.")
            self.show_scan()
            self.refresh()
        except GeometryError as error:
            self.show_error(str(error))

    def preview_cleanup(self):
        if not self.project:
            return
        ids = [self.gap_list.item(i).data(QtCore.Qt.UserRole) for i in range(self.gap_list.count())
               if self.gap_list.item(i).checkState() == QtCore.Qt.Checked]
        selected = self.cleanup_area.copy() if self.cleanup_selected_only.isChecked() else None
        if selected is not None and not selected.any():
            self.show_error("Paint a blue cleanup region, or turn off region-only cleanup.")
            return
        options = CleanupOptions(self.repair_check.isChecked(), self.fragment.value(), self.smoothing.value(),
                                 self.reduction.value() / 100, ids, self.gap_limit.value(), self.auto_fill_gaps.isChecked())
        def ready(preview):
            self.preview = preview
            self.dirty = True
            self.preview_text.setText(preview.summary + ("\n" + "\n".join(preview.warnings) if preview.warnings else ""))
            self.view_mode.setCurrentText("Cleanup preview")
            self.refresh()
        self.run_job("Preparing cleanup preview…", lambda ctx: clean_revision(self.project.current, options, selected, ctx), ready)

    def accept_cleanup(self):
        if self.project and self.preview:
            self.project.commit(self.preview.revision)
            self.reset_selection()
            self.preview_text.setText("Cleanup applied. Existing holds retain their original revision.")
            self.hold_preview = None
            self.dirty = True
            self.show_scan()
            self.refresh()
            self.activity.setText("Repair applied. Next: open 2 Holds, choose Suggest holds or paint a small feature.")

    def discard_cleanup(self):
        self.preview = None
        self.dirty = True
        self.preview_text.setText("Preview discarded")
        self.show_scan()
        self.refresh()

    def clear_selection(self):
        if self.project and not self.worker:
            self.selected[:] = False
            self.cleanup_area[:] = False
            self.hold_preview = None
            self.hold_preview_text.setText("Selection cleared. Choose a suggestion or paint a new feature.")
            self.dirty = True
            self.refresh()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "plotter", None) and not self.worker and self.project:
            active = self.brush_mode.currentText() != "Navigate" and self.view_mode.currentText() == "Working scan"
            if active and event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseMove, QtCore.QEvent.MouseButtonRelease):
                is_press = event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.RightButton
                is_release = event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.RightButton
                if is_press or is_release or self._brush_down:
                    ratio = obj.devicePixelRatioF()
                    position = event.position()
                    interactor = self.plotter.iren.interactor
                    interactor.SetEventInformation(int(position.x() * ratio), int((obj.height() - position.y() - 1) * ratio), 0, 0)
                    if is_press:
                        self._brush_start(interactor, None)
                    elif is_release:
                        self._brush_end(interactor, None)
                    else:
                        self._brush_move(interactor, None)
                    return True
        return super().eventFilter(obj, event)

    def _brush_start(self, obj, event):
        if self.worker or not self.project or self.brush_mode.currentText() == "Navigate" or self.view_mode.currentText() != "Working scan":
            return
        self._brush_down = True
        self._brush_snapshot = None
        if self.brush_mode.currentText() in ("Protect grip", "Unprotect"):
            current = self.project.current
            self._brush_snapshot = Revision(current.mesh.copy(), "Grip protection", protected=current.protected.copy())
        obj.GetInteractorStyle().SetEnabled(0)
        self._paint(obj)

    def _brush_move(self, obj, event):
        if self._brush_down:
            import time
            if time.monotonic() - self._last_paint > 0.045:
                self._paint(obj)
                self._last_paint = time.monotonic()

    def _brush_end(self, obj, event):
        if not self._brush_down:
            return
        self._brush_down = False
        obj.GetInteractorStyle().SetEnabled(1)
        if self._brush_snapshot is not None:
            self.project.commit(self._brush_snapshot)
            self._brush_snapshot = None
            self.preview = None
            self.hold_preview = None
            self.candidates = []
            self.candidate_list.clear()
            self.dirty = True
            self.gap_list.clear()
        self.refresh()

    def _paint(self, interactor):
        from vtkmodules.vtkRenderingCore import vtkCellPicker
        picker = vtkCellPicker()
        picker.SetTolerance(0.005)
        if getattr(self, "scan_actor", None) is None:
            return
        picker.AddPickList(self.scan_actor)
        picker.PickFromListOn()
        x, y = interactor.GetEventPosition()
        if not picker.Pick(x, y, 0, self.plotter.renderer):
            return
        point = np.array(picker.GetPickPosition())
        mesh = self.project.current.mesh
        mode = self.brush_mode.currentText()
        radius = self.brush_radius.value()
        if mode in ("Select hold", "Erase hold"):
            ids = cKDTree(mesh.triangles_center).query_ball_point(point, radius)
            self.selected[ids] = mode == "Select hold"
            self.hold_preview = None
            self.keep_hold_button.setEnabled(False)
            self.discard_hold_button.setEnabled(False)
            self.hold_preview_text.setText("Selection changed. Preview the new shape before keeping it.")
        else:
            ids = cKDTree(mesh.vertices).query_ball_point(point, radius)
            if mode in ("Protect grip", "Unprotect") and self._brush_snapshot:
                self._brush_snapshot.protected[ids] = mode == "Protect grip"
            elif mode in ("Cleanup area", "Erase cleanup area"):
                self.cleanup_area[ids] = mode == "Cleanup area"
        self._update_scan_colors()
        self.dirty = True

    def _scan_colors(self):
        mesh = self.project.current.mesh
        colors = np.tile([128, 162, 159], (len(mesh.faces), 1)).astype(np.uint8)
        if len(self.cleanup_area) == len(mesh.vertices):
            colors[self.cleanup_area[mesh.faces].any(axis=1)] = [104, 159, 224]
        protection = self._brush_snapshot.protected if self._brush_snapshot else self.project.current.protected
        colors[protection[mesh.faces].any(axis=1)] = [77, 220, 172]
        if len(self.selected) == len(mesh.faces):
            colors[self.selected] = [236, 181, 108]
        return colors

    def _update_scan_colors(self):
        if getattr(self, "scan_poly", None) is not None:
            self.scan_poly.cell_data["region"] = self._scan_colors()
            self.plotter.render()
            self.selection_info.setText(f"{self.selected.sum():,} triangles selected · Teal: protected · Amber: hold · Blue: cleanup")

    def set_up_axis(self, index):
        if self.project and not self.worker:
            axis = [1, 2, 0, 1, 2, 0][index]
            self.project.up = np.zeros(3)
            self.project.up[axis] = 1 if index < 3 else -1
            self.candidates = []
            self.candidate_list.clear()
            self.hold_preview = None
            self.hold_preview_text.setText("Scan up changed. Suggest again to check contact in this direction.")
            self.dirty = True

    def suggest(self):
        if not self.project:
            return
        if self.preview:
            self.show_error("Apply or discard the repair preview first, then suggest holds on the working scan.")
            return
        if self.feature_min.value() >= self.feature_max.value():
            self.show_error("The maximum feature size must exceed the minimum.")
            return
        def ready(result):
            candidates, skipped = result
            self.candidates = candidates
            self.candidate_list.clear()
            for i, candidate in enumerate(candidates):
                self.candidate_list.addItem(f"{i + 1:02}  {candidate.explanation}")
            self.activity.setText(f"{len(candidates)} broad-feature candidates for review. {skipped.text}")
            self.hold_preview_text.setText(skipped.text + "\n" + "\n".join(dict.fromkeys(skipped.reasons)))
            self.show_scan()
            if candidates:
                self.candidate_list.setCurrentRow(0)
            else:
                self.hold_preview_text.setText("No suitable broad handhold features found. The scan may contain texture without enough natural grip, or the selection may cut through tall rock. Scan a more distinct feature with low surrounding rock. Manual shape previews remain available.\n\n" + skipped.text + "\n" + "\n".join(dict.fromkeys(skipped.reasons)))
        minimum, maximum, sensitivity = self.feature_min.value(), self.feature_max.value(), self.sensitivity.value()
        revision, up = self.project.current, self.project.up.copy()
        round_boundary = self.prepare_boundary.isChecked()
        def operation(ctx):
            return suggest_holds(revision, up, minimum, maximum, sensitivity, ctx, round_boundary)
        self.run_job("Looking for broad grip features…",
            operation, ready)

    def choose_candidate(self, index):
        if self.project and 0 <= index < len(self.candidates):
            self.hold_preview = None
            self.hold_preview_text.setText("Click Preview hold from selection to inspect this suggestion.")
            self.selected[:] = False
            self.selected[self.candidates[index].faces] = True
            self.view_mode.setCurrentText("Working scan")
            self.brush_mode.setCurrentText("Select hold")
            self.refresh()

    def create_hold(self):
        if not self.project or not self.selected.any():
            self.show_error("Select a suggested feature or paint a region with the Select hold brush.")
            return
        if self.preview:
            self.show_error("Apply or discard the repair preview first. Hold selection uses the working scan.")
            return
        faces = np.flatnonzero(self.selected)
        name = f"Hold {len(self.project.holds) + 1:02}"
        def ready(hold):
            self.hold_preview = hold
            self.dirty = True
            self.shape_fields(hold)
            self.hold_preview_text.setText(hold.preparation + "\n" + review_text(hold) + "\nInspect green rock and amber support before keeping.")
            self.view_mode.setCurrentText("Hold preview")
            self.refresh(reset_camera=True)
            QtCore.QTimer.singleShot(0, lambda: self.tabs.widget(1).ensureWidgetVisible(self.keep_hold_button))
        round_boundary = self.prepare_boundary.isChecked()
        cached = next((c.prepared_hold for c in self.candidates if c.prepared_hold is not None and
                       c.prepared_hold.revision_id == self.project.current.id and np.array_equal(np.sort(c.faces), faces)), None)
        if cached is not None and round_boundary:
            cached = deepcopy(cached)
            cached.id = uuid.uuid4().hex
            cached.name = name
            ready(cached)
            return
        def operation(ctx):
            hold = (prepare_feature(self.project.current, faces, name, ctx) if round_boundary else
                    prepare_hold(self.project.current, faces, name, ctx, round_boundary=False))
            hold.grip_review = assess_grip(hold, self.project.up, ctx)
            return hold
        self.run_job("Preparing a flat-backed shape preview…", operation, ready)

    def accept_hold_preview(self):
        if not self.project or self.hold_preview is None or self.worker:
            return
        if self.hold_preview.revision_id != self.project.current.id:
            self.show_error("The scan has changed. Preview the hold again before keeping it.")
            return
        self.project.holds.append(self.hold_preview)
        self.hold_preview = None
        self.hold_index = len(self.project.holds) - 1
        self.dirty = True
        self.update_hold_list()
        self.select_hold(self.hold_index)
        self.hold_preview_text.setText(review_text(self.hold) + "\nSaved in the library. Open 3 Mounts to add hardware.")
        self.activity.setText("Hold kept. Open 3 Mounts to choose hardware and add mounting holes.")

    def discard_hold_preview(self):
        self.hold_preview = None
        self.dirty = True
        self.hold_preview_text.setText("Preview discarded. Refine the amber selection and try again.")
        self.show_scan()
        self.refresh()

    def update_hold_list(self):
        self.hold_list.blockSignals(True)
        self.hold_list.clear()
        if self.project:
            for hold in self.project.holds:
                self.hold_list.addItem(hold.name)
            self.hold_list.setCurrentRow(self.hold_index)
        self.hold_list.blockSignals(False)

    def select_hold(self, index):
        if not self.project or index < 0:
            return
        self.hold_index = index
        hold = self.hold
        if not hold:
            return
        self.hold_preview_text.setText(hold.preparation + "\n" + review_text(hold))
        self.shape_fields(hold)
        self.mount_mode.setCurrentText(hold.mode)
        self.update_mount_list()
        self.view_mode.setCurrentText("Hold")
        self.refresh(reset_camera=True)

    def shape_fields(self, hold):
        self.backing.setMinimum(0. if hold.edge_style == "natural-base" else .5)
        self.backing.setValue(hold.backing)
        self.edge_flare.setValue(hold.edge_flare)
        self.edge_flare.setEnabled(hold.edge_style != "natural-base")
        self.hold_scale.setValue(hold.scale * 100)
        self.tilt_x.setValue(hold.tilt_x)
        self.tilt_y.setValue(hold.tilt_y)

    def rename_hold(self, item):
        if not self.hold:
            return
        name, accepted = QtWidgets.QInputDialog.getText(self, "Name hold", "Name", text=self.hold.name)
        if accepted and name.strip():
            self.hold.name = name.strip()
            self.update_hold_list()
            self.dirty = True

    def remove_hold(self):
        if self.hold:
            del self.project.holds[self.hold_index]
            self.hold_index = -1
            self.update_hold_list()
            self.update_mount_list()
            self.dirty = True
            self.show_scan()
            self.refresh()

    def rebuild_hold(self):
        target = self.hold_preview if self.hold_preview is not None else self.hold
        if target is None:
            self.show_error("Create a preview or select a hold first.")
            return
        editing_preview = self.hold_preview is not None
        edited = deepcopy(target)
        edited.backing = self.backing.value()
        edited.edge_flare = self.edge_flare.value()
        edited.scale = self.hold_scale.value() / 100
        edited.tilt_x, edited.tilt_y = self.tilt_x.value(), self.tilt_y.value()
        edited.result = None
        def operation(ctx):
            edited.solid = build_solid(edited, ctx)
            edited.grip_review = assess_grip(edited, self.project.up, ctx)
            return edited
        def ready(result):
            self.dirty = True
            if editing_preview:
                self.hold_preview = result
            else:
                self.project.holds[self.hold_index] = result
                self.dirty = True
            self.hold_preview_text.setText(result.preparation + "\n" + review_text(result))
            self.update_mount_list()
            self.view_mode.setCurrentText("Hold preview" if editing_preview else "Hold")
            self.refresh()
        self.run_job("Updating backing geometry…", operation, ready)

    def use_tapered_defaults(self):
        target = self.hold_preview if self.hold_preview is not None else self.hold
        if target is None:
            self.show_error("Select a saved hold or paint a feature first.")
            return
        if target.revision_id != self.project.current.id:
            self.show_error("This design belongs to another scan revision. Select its feature on the current working scan to make a new preview.")
            return
        self.selected[:] = False
        self.selected[target.face_indices] = True
        self.prepare_boundary.setChecked(True)
        self.create_hold()

    def update_profile_summary(self, *_):
        if not self.project:
            self.profile_summary.setText("Prototype profiles will be available when you import a scan or open a sample.")
            return
        kind = "bolt" if self.mount_mode.currentText() == "bolt-on" else "screw"
        profile = self.project.profiles[kind]
        self.profile_summary.setText(f"{profile.name}\n{profile.fit_summary()}")

    def edit_profile(self, kind):
        if not self.project:
            self.show_error("Import a scan or open a sample first.")
            return False
        dialog = ProfileDialog(self.project.profiles[kind], self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self.project.profiles[kind] = dialog.profile()
            self.update_profile_summary()
            self.dirty = True
            return True
        return False

    def ensure_profile(self, kind):
        try:
            self.project.profiles[kind].validate()
            return True
        except GeometryError:
            return self.edit_profile(kind)

    def auto_mounts(self):
        if not self.hold:
            self.show_error("Create or select a hold first.")
            return
        mode = self.mount_mode.currentText()
        anti = self.anti_rotation.isChecked()
        if mode == "bolt-on" and not self.ensure_profile("bolt"):
            return
        if (mode == "screw-on" or anti) and not self.ensure_profile("screw"):
            return
        if any(m.manual for m in self.hold.mounts):
            answer = QtWidgets.QMessageBox.question(self, "Regenerate mount positions", "Replace your manually adjusted layout with a new automatic layout?")
            if answer != QtWidgets.QMessageBox.Yes:
                return
        hold = deepcopy(self.hold)
        count = self.screw_count.value()
        def ready(mounts):
            self.hold.mounts = mounts
            self.hold.mode = mode
            self.hold.result = None
            self.dirty = True
            self.update_mount_list()
            self.view_mode.setCurrentText("Hold")
            self.refresh()
        self.run_job("Positioning mounts…", lambda ctx: generate_mounts(hold, self.project.profiles["bolt"],
                     self.project.profiles["screw"], mode, count, anti, ctx), ready)

    def update_mount_list(self):
        old_index = self.mount_list.currentRow()
        self.mount_list.blockSignals(True)
        self.mount_list.clear()
        if self.hold:
            for i, mount in enumerate(self.hold.mounts):
                self.mount_list.addItem(f"{i + 1:02}  {mount.role} · Ø{mount.profile.bore:.2f} mm" + (" · edited" if mount.manual else ""))
        self.mount_list.blockSignals(False)
        if self.mount_list.count():
            self.mount_list.setCurrentRow(max(0, min(old_index, self.mount_list.count() - 1)))
        self.check_mounts()

    def select_mount(self, index):
        if self.hold and 0 <= index < len(self.hold.mounts):
            mount = self.hold.mounts[index]
            self.mount_x.setValue(mount.x)
            self.mount_y.setValue(mount.y)

    def check_mounts(self):
        self.update_profile_summary()
        if not self.hold:
            self.mount_status.setText("Select a hold to generate mounting geometry.")
            return False
        try:
            validate_mounts(self.hold)
            self.mount_status.setText("Geometry fits the current allowances. Preview the drilled model." if self.hold.mounts else "No holes. Generate a layout or export a solid shape prototype.")
            return True
        except GeometryError as error:
            self.mount_status.setText(str(error))
            return False

    def mount_edited(self):
        self.hold.result = None
        self.dirty = True
        self.update_mount_list()
        self.refresh()

    def set_mount_position(self):
        index = self.mount_list.currentRow()
        if self.hold and 0 <= index < len(self.hold.mounts):
            mount = self.hold.mounts[index]
            mount.x, mount.y = self.mount_x.value(), self.mount_y.value()
            mount.manual = True
            self.mount_edited()

    def edit_mount_profile(self):
        index = self.mount_list.currentRow()
        if self.hold and 0 <= index < len(self.hold.mounts):
            mount = self.hold.mounts[index]
            dialog = ProfileDialog(mount.profile, self)
            if dialog.exec() == QtWidgets.QDialog.Accepted:
                mount.profile = dialog.profile()
                mount.manual = True
                self.mount_edited()

    def add_mount(self):
        if self.hold and self.ensure_profile("screw"):
            self.hold.mounts.append(Mount(0, 0, deepcopy(self.project.profiles["screw"]), "screw", True))
            self.mount_edited()
            self.mount_list.setCurrentRow(len(self.hold.mounts) - 1)

    def delete_mount(self):
        index = self.mount_list.currentRow()
        if self.hold and 0 <= index < len(self.hold.mounts):
            del self.hold.mounts[index]
            self.mount_edited()

    def drag_mount(self, point, index):
        if not self.hold or self.updating or self.worker or index >= len(self.hold.mounts):
            return
        mount = self.hold.mounts[index]
        mount.x, mount.y = float(point[0]), float(point[1])
        mount.manual = True
        self.hold.result = None
        self.dirty = True
        self.mount_list.setCurrentRow(index)
        QtCore.QTimer.singleShot(0, self.mount_edited)

    def drill_preview(self):
        if not self.hold:
            return
        hold = deepcopy(self.hold)
        def ready(mesh):
            self.hold.result = mesh
            self.dirty = True
            self.view_mode.setCurrentText("Hold")
            self.refresh()
            self.activity.setText("Drilled solid validated · ready to inspect and export")
        self.run_job("Cutting and checking the hold…", lambda ctx: apply_mounts(hold, ctx), ready)

    def export_dialog(self):
        if not self.hold:
            self.show_error("Select a hold to export.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export printable prototype", self.hold.name + ".stl", "STL mesh (*.stl)")
        if not path:
            return
        if not path.lower().endswith(".stl"):
            path += ".stl"
        hold = deepcopy(self.hold)
        def ready(result):
            self.hold.result = result[0]
            self.activity.setText(f"Exported {Path(result[1]).name} and dimensions report. Import into your slicer in millimeters.")
        self.run_job("Validating and exporting…", lambda ctx: export_hold(hold, path, ctx), ready)

    def refresh(self, reset_camera=False):
        if self.updating:
            return
        self.updating = True
        camera = self.plotter.camera_position
        self.plotter.clear_sphere_widgets()
        self.plotter.clear()
        self.plotter.enable_lightkit()
        self.scan_actor = None
        self.scan_poly = None
        self.plotter.add_axes(color="#a4b7b9")
        self.save_button.setEnabled(self.project is not None)
        self.export_button.setEnabled(self.hold is not None)
        self.accept_button.setEnabled(self.preview is not None)
        self.reject_button.setEnabled(self.preview is not None)
        self.keep_hold_button.setEnabled(self.hold_preview is not None)
        self.discard_hold_button.setEnabled(self.hold_preview is not None)
        self.undo_button.setEnabled(bool(self.project and self.project.cursor > 0))
        self.redo_button.setEnabled(bool(self.project and self.project.cursor < len(self.project.revisions) - 1))
        try:
            if not self.project:
                sample = polydata(demo_scan("rounded", 25))
                self.plotter.add_mesh(sample, color="#94b2a8", smooth_shading=True, specular=0.12)
                self.plotter.add_text("A surface. A possibility.", position="upper_left", font_size=20, color="#dfeee7")
                self.plotter.add_text("Import a phone mesh or explore a synthetic sample", position="lower_left", font_size=11, color="#98b3b2")
                self.plotter.view_isometric()
            else:
                mesh = self.project.current.mesh
                self.scan_info.setText(f"{self.project.source_name}\n\n{len(mesh.faces):,} triangles\n" + " × ".join(f"{x:.0f}" for x in mesh.extents) + f" mm\nRevision {self.project.cursor + 1}: {self.project.current.label}")
                mode = self.view_mode.currentText()
                if mode == "Hold preview" and self.hold_preview is not None:
                    self._render_hold(self.hold_preview)
                elif mode == "Hold" and self.hold and self.hold.solid is not None:
                    self._render_hold()
                else:
                    display = self.project.original if mode == "Original scan" else (self.preview.revision.mesh if mode == "Cleanup preview" and self.preview else mesh)
                    self.scan_poly = polydata(display)
                    if mode == "Working scan":
                        self.scan_poly.cell_data["region"] = self._scan_colors()
                        self.scan_actor = self.plotter.add_mesh(self.scan_poly, scalars="region", rgb=True, smooth_shading=False, show_scalar_bar=False, lighting=True, ambient=0.15, diffuse=0.8)
                    elif mode == "Cleanup preview" and self.preview and self.preview.added is not None:
                        colors = np.tile([158, 189, 178], (len(display.faces), 1)).astype(np.uint8)
                        # Gap triangles are appended last by cleanup. Color them
                        # directly to avoid two coincident surfaces flickering.
                        colors[-len(self.preview.added.faces):] = [246, 211, 108]
                        self.scan_poly.cell_data["repair"] = colors
                        self.scan_actor = self.plotter.add_mesh(self.scan_poly, scalars="repair", rgb=True,
                            smooth_shading=False, show_scalar_bar=False)
                    else:
                        self.scan_actor = self.plotter.add_mesh(self.scan_poly, color="#9ebdb2", smooth_shading=False)
                    if self.compare.isChecked() and mode != "Original scan":
                        self.plotter.add_mesh(polydata(self.project.original), style="wireframe", color="#d9ab76", opacity=0.22, pickable=False)
                    if mode == "Cleanup preview" and self.preview and self.preview.removed is not None:
                        self.plotter.add_mesh(polydata(self.preview.removed), color="#eb7a72", opacity=0.85, pickable=False)
                    if mode == "Working scan" and self.gap_list.currentRow() >= 0 and hasattr(self, "gaps"):
                        gap = self.gaps[self.gap_list.currentRow()]
                        points = mesh.vertices[gap["vertices"]]
                        self.plotter.add_lines(np.vstack([points, points[0]]), connected=True, color="#f58b6e", width=5)
                    self.plotter.add_text(f"{mode.upper()}  /  mm", position="upper_left", font_size=11, color="#b7cec7")
                self.selection_info.setText(f"{self.selected.sum():,} triangles selected · Teal: protected · Amber: hold · Blue: cleanup")
            if reset_camera:
                self.plotter.view_isometric()
                self.plotter.reset_camera()
            elif self.project:
                self.plotter.camera_position = camera
            self.plotter.render()
        finally:
            self.updating = False

    def _render_hold(self, preview_hold=None):
        hold = preview_hold if preview_hold is not None else self.hold
        mesh = hold.result if hold.result is not None else hold.solid
        display = polydata(mesh)
        if hold.result is None:
            colors = np.tile([161, 198, 181], (len(mesh.faces), 1)).astype(np.uint8)
            colors[generated_faces(hold) | (mesh.triangles[:, :, 2].max(axis=1) < 1e-6)] = [209, 158, 99]
            display.cell_data["Surface origin"] = colors
        if self.cutaway.isChecked():
            x = mesh.bounds[0, 0] + mesh.extents[0] * self.cut_position.value() / 100
            display = display.clip(normal="x", origin=(x, 0, 0))
        if hold.result is None:
            self.plotter.add_mesh(display, scalars="Surface origin", rgb=True, smooth_shading=False, specular=0.12)
            self.plotter.add_text("Green: scanned rock   |   Amber: generated wall return", position="lower_left", font_size=9, color="#c5e3d5")
        else:
            self.plotter.add_mesh(display, color="#a1c6b5", smooth_shading=False, specular=0.12)
        extent = mesh.extents
        plane = pv.Plane(center=(mesh.centroid[0], mesh.centroid[1], -0.25), direction=(0, 0, 1),
                         i_size=extent[0] * 1.2, j_size=extent[1] * 1.2, i_resolution=10, j_resolution=10)
        self.plotter.add_mesh(plane, color="#47626a", opacity=0.25, show_edges=True, pickable=False)
        for index, mount in enumerate(hold.mounts):
            z = mesh.bounds[1, 2] + 4
            self.plotter.add_lines(np.array([[mount.x, mount.y, -1], [mount.x, mount.y, z]]), color="#f0b77c", width=2)
            ring = pv.Circle(radius=max(mount.profile.head_diameter / 2, 1), resolution=64)
            ring.translate([mount.x, mount.y, z], inplace=True)
            self.plotter.add_mesh(ring, style="wireframe", color="#f0b77c", line_width=2, pickable=False)
            if self.drag_mounts.isChecked() and not self.worker:
                self.plotter.add_sphere_widget(lambda point, i=index: self.drag_mount(point, i),
                    center=(mount.x, mount.y, z), radius=max(2, extent.max() * 0.012),
                    color="#f0b77c", selected_color="#f9dfb8", test_callback=False)
        state = "HOLD PREVIEW" if preview_hold is not None else ("DRILLED" if hold.result is not None else "NATURAL FEATURE" if hold.edge_style == "natural-base" else "SUPPORTED SHAPE")
        self.plotter.add_text(f"{hold.name.upper()}  /  {state}\n" +
                             " × ".join(f"{x:.1f}" for x in extent) + " mm", position="upper_left", font_size=11, color="#c5e3d5")

    def closeEvent(self, event):
        if self.worker:
            self.show_error("Cancel the active operation and wait for it to finish before closing.")
            event.ignore()
        elif not self.confirm_replace():
            event.ignore()
        else:
            self.plotter.close()
            event.accept()


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Rockform")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
