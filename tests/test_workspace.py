from copy import deepcopy
import numpy as np
from PySide6 import QtWidgets
from rockform.app import MainWindow
from rockform.model import Project
from rockform.storage import load_project, save_project
from rockform.geometry import demo_scan
from rockform.features import prepare_feature
from rockform.mounts import generate_mounts
from test_ui import wait_for_worker


def close(window):
    if window.worker:
        window.cancel_job()
        wait_for_worker(window)
    window.dirty = False
    window.close()


def test_save_dialog_restores_unkept_preview_selection_pending_edits_and_view(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    first, second = MainWindow(), MainWindow()
    errors = []
    first.show_error = second.show_error = errors.append
    path = tmp_path / 'unfinished.rockform'
    try:
        first.set_project(Project(demo_scan('rounded', 15), 'Draft fixture'))
        first.project_path = str(path)
        first.selected[:] = True
        first.cleanup_area[:7] = True
        first.create_hold()
        wait_for_worker(first)
        assert not errors
        draft = first.hold_preview
        first.tabs.setCurrentIndex(1)
        first.hold_scale.setValue(123.)  # An edit not yet rebuilt must survive too.
        first.plotter.view_xy()
        first.plotter.camera.parallel_projection = True
        camera = np.array(list(first.plotter.camera_position))
        first.save_dialog()
        wait_for_worker(first)
        assert not errors and not first.dirty
        restored = load_project(path)
        assert not restored.holds  # Save never silently accepts a preview.
        second.set_project(restored, str(path))
        assert second.view_mode.currentText() == 'Hold preview'
        assert second.tabs.currentIndex() == 1
        assert second.hold_preview.id == draft.id
        assert np.array_equal(second.hold_preview.patch.vertices, draft.patch.vertices)
        assert np.array_equal(second.selected, first.selected)
        assert np.array_equal(second.cleanup_area, first.cleanup_area)
        assert second.hold_scale.value() == 123.
        assert second.hold_preview.scale == 1.
        assert np.allclose(list(second.plotter.camera_position), camera)
        assert second.plotter.camera.parallel_projection
        assert not second.dirty
        second.rebuild_hold()
        wait_for_worker(second)
        assert not errors and second.dirty
        assert second.hold_preview.scale == 1.23
        second.accept_hold_preview()
        assert len(second.project.holds) == 1
        second.save_dialog()
        wait_for_worker(second)
        latest = load_project(path)
        assert latest.pending_hold is None and len(latest.holds) == 1
    finally:
        close(first); close(second)


def test_pending_repair_is_restored_without_applying_until_user_accepts(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    first, second = MainWindow(), MainWindow()
    errors = []
    first.show_error = second.show_error = errors.append
    path = tmp_path / 'repair.rockform'
    try:
        first.set_project(Project(demo_scan('rounded', 13), 'Repair fixture'))
        first.project_path = str(path)
        first.smoothing.setValue(2)
        first.preview_cleanup()
        wait_for_worker(first)
        assert not errors and first.preview
        expected = first.preview.revision.mesh.vertices.copy()
        first.save_dialog()
        wait_for_worker(first)
        second.set_project(load_project(path), str(path))
        assert second.project.cursor == 0
        assert second.view_mode.currentText() == 'Cleanup preview'
        assert second.preview is not None and second.accept_button.isEnabled()
        assert np.array_equal(second.preview.revision.mesh.vertices, expected)
        assert second.smoothing.value() == 2
        second.accept_cleanup()
        assert second.project.cursor == 1
        assert np.array_equal(second.project.current.mesh.vertices, expected)
    finally:
        close(first); close(second)


def test_kept_hold_and_manual_mount_restore_selected_and_old_projects_show_hold(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    path = tmp_path / 'kept.rockform'
    try:
        project = Project(demo_scan('rounded', 13), 'Saved holds')
        hold = prepare_feature(project.current, np.arange(len(project.current.mesh.faces)))
        hold.mounts = generate_mounts(hold, project.profiles['bolt'], project.profiles['screw'], anti_rotation=False)
        hold.mounts[0].manual = True
        second = deepcopy(hold); second.id += 'second'; second.name = 'Second hold'
        project.holds = [hold, second]
        window.set_project(project)
        assert window.hold is hold and window.view_mode.currentText() == 'Hold'
        window.select_hold(1)
        window.tabs.setCurrentIndex(2)
        window.cutaway.setChecked(True)
        window.capture_workspace()
        save_project(project, path)
        window.set_project(load_project(path), str(path))
        assert window.hold.id == second.id
        assert window.hold.mounts == second.mounts
        assert window.tabs.currentIndex() == 2 and window.cutaway.isChecked()
        assert window.view_mode.currentText() == 'Hold'
        window.backing.setValue(4.)
        assert window.dirty
    finally:
        close(window)
