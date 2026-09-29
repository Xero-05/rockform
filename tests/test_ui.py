import time
import numpy as np
from PySide6 import QtWidgets, QtCore
from PySide6.QtTest import QTest
from rockform.app import MainWindow
from rockform.widgets import STYLE
from rockform.model import Project, HardwareProfile
from rockform.geometry import demo_scan
from rockform.detection import Candidate


def wait_for_worker(window):
    deadline = time.monotonic() + 60
    while window.worker and time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        time.sleep(.01)  # Release the Python GIL so the geometry worker can run.
    assert window.worker is None, "Background operation did not finish"


def test_native_workflow_and_manual_mount_edits():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(STYLE)
    window = MainWindow()
    errors = []
    window.show_error = errors.append
    window.show()
    try:
        project = Project(demo_scan("rounded", 15), "Synthetic UI test")
        window.set_project(project)
        window.smoothing.setValue(1)
        window.preview_cleanup()
        wait_for_worker(window)
        assert not errors, errors
        assert window.preview is not None
        assert window.view_mode.currentText() == "Cleanup preview"
        window.accept_cleanup()
        assert project.cursor == 1
        window.undo()
        assert project.cursor == 0
        window.redo()
        assert project.cursor == 1
        window.feature_max.setValue(300)  # Include the entire natural root on this coarse fixture.
        window.suggest()
        wait_for_worker(window)
        assert not errors, errors
        assert window.candidate_list.count() > 0
        # Use a deterministic connected region for extraction.
        window.selected = (np.abs(project.current.mesh.triangles[:, :, :2]) < 95).all(axis=(1, 2))
        window.create_hold()
        wait_for_worker(window)
        assert not errors, errors
        assert window.hold is None
        assert window.hold_preview is not None
        window.accept_hold_preview()
        assert window.hold is not None
        assert project.profiles["bolt"].bore == 10.5
        assert "clear width" in window.profile_summary.text()
        window.auto_mounts()
        wait_for_worker(window)
        assert not errors, errors
        assert len(window.hold.mounts) == 1
        original_x = window.hold.mounts[0].x
        window.drag_mount((original_x + 1, window.hold.mounts[0].y, 100), 0)
        QTest.qWait(50)
        assert window.hold.mounts[0].manual
        assert window.hold.mounts[0].x == original_x + 1
        window.drill_preview()
        wait_for_worker(window)
        assert not errors, errors
        assert window.hold.result.is_volume
        window.cutaway.setChecked(True)
        QTest.qWait(50)
        assert window.hold.mounts[0].x == original_x + 1
    finally:
        if window.worker:
            window.cancel_job()
            wait_for_worker(window)
        window.dirty = False
        window.close()


def test_protection_brush_commits_undoable_revision():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    window.show()
    try:
        window.set_project(Project(demo_scan("rounded", 15), "Brush test"))
        window.plotter.view_xy()
        window.plotter.reset_camera()
        QTest.qWait(200)
        window.brush_mode.setCurrentText("Protect grip")
        window.brush_radius.setValue(40)
        renderer = window.plotter.renderer
        renderer.SetWorldPoint(0, 0, 65, 1)
        renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        widget = window.plotter.interactor
        ratio = widget.devicePixelRatioF()
        position = QtCore.QPoint(int(x / ratio), int(widget.height() - y / ratio - 1))
        QTest.mousePress(widget, QtCore.Qt.RightButton, pos=position)
        QTest.mouseRelease(widget, QtCore.Qt.RightButton, pos=position)
        QTest.qWait(100)
        assert window.project.current.protected.any()
        window.undo()
        assert not window.project.current.protected.any()
    finally:
        window.dirty = False
        window.close()


def test_preview_requires_acceptance_and_selection_changes_invalidate_it():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        window.set_project(Project(demo_scan("rounded", 9), "Preview test"))
        window.selected[:] = True
        window.create_hold()
        wait_for_worker(window)
        assert window.hold_preview is not None
        assert not window.project.holds
        cached = window.hold_preview
        window.candidates = [Candidate(np.flatnonzero(window.selected), 1, "Test", cached)]
        window.clear_selection()
        assert window.hold_preview is None
        assert not window.keep_hold_button.isEnabled()
        window.choose_candidate(0)
        window.create_hold()
        window.accept_hold_preview()
        window.choose_candidate(0)
        window.create_hold()
        window.accept_hold_preview()
        assert len({hold.id for hold in window.project.holds}) == 2
        window.show_error("Choose a smaller area.")
        assert not window.message_panel.isHidden()
        assert window.message_text.text() == "Choose a smaller area."
        assert not any(isinstance(w, QtWidgets.QMessageBox) for w in app.topLevelWidgets())
    finally:
        if window.worker:
            window.cancel_job()
            wait_for_worker(window)
        window.dirty = False
        window.close()


def test_detected_units_dialog_does_not_double_scale(tmp_path):
    from rockform.storage import import_scan
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    mesh = demo_scan("rounded", 9)
    mesh.vertices /= 1000
    path = tmp_path / "meters.obj"
    path.write_text("# Unit: meter\n" + mesh.export(file_type="obj"))
    window = MainWindow()
    project = import_scan(path)
    def confirm():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QtWidgets.QDialog)
        units = dialog.findChild(QtWidgets.QComboBox)
        assert units.currentText() == "Meters"
        dialog.accept()
    QtCore.QTimer.singleShot(10, confirm)
    try:
        window.confirm_scale(project)
        assert np.allclose(window.project.original.extents, mesh.extents * 1000)
        assert window.project.scale_factor == 1000
    finally:
        window.dirty = False
        window.close()
