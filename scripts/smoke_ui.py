"""Run the native UI and capture its own window for visual QA; no external app control."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6 import QtWidgets, QtCore
from rockform.app import MainWindow
from rockform.widgets import STYLE
from rockform.model import Project, HardwareProfile
from rockform.geometry import demo_scan, extract_hold
from rockform.mounts import generate_mounts, apply_mounts
import numpy as np

app = QtWidgets.QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
window = MainWindow()
window.show()
output = Path("test-output")
output.mkdir(exist_ok=True)

def capture():
    try:
        project = Project(demo_scan("ledge", 25), "Synthetic ledge · demonstration")
        window.set_project(project)
        app.processEvents()
        window.grab().save(str(output / "scan-window.png"))
        ids = np.flatnonzero((np.abs(project.current.mesh.triangles[:, :, :2]) < 90).all(axis=(1, 2)))
        hold = extract_hold(project.current, ids, "Ledge 01")
        bolt = HardwareProfile("Demo bolt", 10, .4, 18, 6)
        screw = HardwareProfile("Demo screw", 5, .3, 10, 3, "countersink")
        hold.mounts = generate_mounts(hold, bolt, screw)
        hold.result = apply_mounts(hold)
        project.holds.append(hold)
        window.update_hold_list()
        window.select_hold(0)
        window.tabs.setCurrentIndex(2)
        app.processEvents()
        window.grab().save(str(output / "mount-window.png"))
        window.cutaway.setChecked(True)
        app.processEvents()
        window.grab().save(str(output / "cutaway-window.png"))
        print("UI smoke test passed; screenshots written to test-output", flush=True)
        window.dirty = False
        window.close()
        app.quit()
    except Exception:
        import traceback
        traceback.print_exc()
        window.dirty = False
        window.close()
        app.exit(1)

QtCore.QTimer.singleShot(1000, capture)
sys.exit(app.exec())
