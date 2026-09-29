"""Exercise the real scan workflow in the app's own Qt window, then capture it."""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6 import QtWidgets
from rockform.app import MainWindow
from rockform.storage import load_project
from rockform.widgets import STYLE

project_path, destination = sys.argv[1:3]
output = Path(destination)
output.mkdir(parents=True, exist_ok=True)
app = QtWidgets.QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
window = MainWindow()
window.show()
errors = []
window.show_error = errors.append


def wait():
    deadline = time.monotonic() + 120
    while window.worker and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.worker is None, "Background operation timed out"
    assert not errors, errors
    app.processEvents()


try:
    project = load_project(project_path)
    project.cursor = 0
    project.holds = []
    window.set_project(project)
    window.face_scan()
    window.preview_cleanup()
    wait()
    assert window.preview.added is not None
    colors = window.scan_poly.cell_data["repair"]
    assert (colors == [246, 211, 108]).all(axis=1).sum() == len(window.preview.added.faces)
    window.grab().save(str(output / "repair-preview.png"))
    window.accept_cleanup()
    window.tabs.setCurrentIndex(1)
    window.suggest()
    wait()
    assert len(window.candidates) == 6
    window.create_hold()
    wait()
    assert window.hold_preview is not None
    assert not project.holds
    window.grab().save(str(output / "hold-preview.png"))
    window.accept_hold_preview()
    assert len(project.holds) == 1
    assert window.hold.solid.is_volume
    print("Real scan UI: repair preview, Apply, six checked suggestions, Preview hold, and Keep hold passed.")
finally:
    if window.worker:
        window.cancel_job()
        wait()
    window.dirty = False
    window.close()
