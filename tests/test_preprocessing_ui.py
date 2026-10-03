import numpy as np
from PySide6 import QtCore, QtWidgets
from PySide6.QtTest import QTest
from rockform.app import MainWindow
from rockform.model import Project
from rockform.storage import save_project, load_project
from test_preprocessing import surface
from test_ui import wait_for_worker


def finish(window):
    if window.worker:
        window.cancel_job()
        wait_for_worker(window)
    window.dirty = False
    window.close()


def test_default_import_queue_paint_crop_save_reopen_and_repair(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window, reopened = MainWindow(), MainWindow()
    errors = []
    window.show_error = errors.append
    project = Project(surface(), 'dense scan')
    def confirm():
        dialog = app.activeModalWidget()
        if not isinstance(dialog, QtWidgets.QDialog):
            QtCore.QTimer.singleShot(10, confirm)
            return
        assert dialog.findChild(QtWidgets.QCheckBox, 'auto_decimate_import').isChecked()
        dialog.accept()
    window.show()
    try:
        QtCore.QTimer.singleShot(50, confirm)
        # Exercise scale confirmation as a worker callback, just like Import scan.
        window.run_job('Import fixture', lambda ctx: project, window.confirm_scale)
        wait_for_worker(window)
        assert not errors
        assert project.cursor == 1
        assert len(project.current.mesh.faces) < len(project.original.faces)
        assert window.brush_mode.currentText() == 'Area of interest'
        window.plotter.view_xy(); window.plotter.reset_camera()
        window.brush_radius.setValue(20)
        QTest.qWait(100)
        renderer = window.plotter.renderer
        renderer.SetWorldPoint(-5, -20, 0, 1); renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        widget = window.plotter.interactor
        ratio = widget.devicePixelRatioF()
        point = QtCore.QPoint(int(x / ratio), int(widget.height() - y / ratio - 1))
        QTest.mousePress(widget, QtCore.Qt.RightButton, pos=point)
        QTest.mouseRelease(widget, QtCore.Qt.RightButton, pos=point)
        assert window.interest_area.any()
        assert window.crop_button.isEnabled()
        window.preview_cleanup()
        assert 'selected area before repair' in errors.pop()
        assert window.worker is None
        window.interest_margin.setValue(10)
        window.preview_interest(); wait_for_worker(window)
        assert not errors
        assert window.preview is not None
        assert window.accept_button.text() == 'Apply selected area'
        window.capture_workspace()
        path = tmp_path / 'area-draft.rockform'
        save_project(project, path)
        reopened.set_project(load_project(path), str(path))
        assert reopened.preview.revision.processing['operation'] == 'area-of-interest'
        assert np.array_equal(window.interest_area, reopened.interest_area)
        assert reopened.interest_margin.value() == 10
        assert reopened.project.cursor == 1
        reopened.accept_cleanup()
        assert reopened.project.cursor == 2
        assert not reopened.interest_area.any()
        assert reopened.project.current.crop_boundary.any()
        before = len(reopened.project.current.mesh.faces)
        reopened.preview_cleanup(); wait_for_worker(reopened)
        assert reopened.preview.revision.mesh.bounds[1, 0] < 50
        reopened.accept_cleanup()
        reopened.undo()
        assert len(reopened.project.current.mesh.faces) == before
        reopened.undo()
        assert len(reopened.project.current.mesh.faces) == len(window.project.current.mesh.faces)
    finally:
        finish(window); finish(reopened)


def test_import_opt_out_and_changing_margin_invalidates_crop():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    project = Project(surface(31), 'unchanged scan')
    def confirm():
        dialog = app.activeModalWidget()
        dialog.findChild(QtWidgets.QCheckBox, 'auto_decimate_import').setChecked(False)
        dialog.accept()
    try:
        QtCore.QTimer.singleShot(10, confirm)
        window.confirm_scale(project)
        assert window.worker is None and project.cursor == 0
        window.interest_area[:] = np.linalg.norm(project.current.mesh.triangles_center[:, :2], axis=1) < 15
        window.preview_interest(); wait_for_worker(window)
        assert window.preview
        window.interest_margin.setValue(5)
        assert window.preview is None
        assert not window.accept_button.isEnabled()
        window.preview_interest(); wait_for_worker(window)
        window.clear_interest()
        assert window.preview is None and not window.interest_area.any()
        assert project.cursor == 0
    finally:
        finish(window)
