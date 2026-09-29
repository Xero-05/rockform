from dataclasses import asdict
import os
os.environ.setdefault("QT_API", "pyside6")
from PySide6 import QtCore, QtWidgets
from .model import HardwareProfile, TaskContext, Cancelled, default_profiles


STYLE = """
QMainWindow, QDialog { background: #111b20; color: #e4eceb; }
QWidget { color: #dde7e6; font-family: 'Segoe UI'; font-size: 13px; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #f3f5ed; }
QLabel#eyebrow { color: #73c8b4; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
QLabel#muted { color: #98aaa9; }
QLabel#section { font-size: 16px; font-weight: 600; color: #f0f3e8; }
QPushButton { background: #26373e; border: 1px solid #3a4c52; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #344c51; border-color: #73c8b4; }
QPushButton:disabled { color: #647879; background: #1a282e; border-color: #26373e; }
QPushButton#primary { background: #9edbc1; color: #142e29; border: none; font-weight: 650; }
QPushButton#primary:hover { background: #bce8d7; }
QPushButton#primary:disabled { background: #3d5a51; color: #718e84; }
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit { background: #17272e; border: 1px solid #3a4c52; border-radius: 4px; padding: 5px; min-height: 22px; }
QComboBox QAbstractItemView { background: #1c2d34; selection-background-color: #376957; }
QListWidget, QTableWidget { background: #15242b; border: 1px solid #2d4147; border-radius: 6px; alternate-background-color: #1b2e35; }
QListWidget::item { padding: 9px 5px; border-bottom: 1px solid #23383e; }
QListWidget::item:selected { background: #305b51; color: #ecfff8; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #1b2b32; padding: 10px 13px; color: #91a9aa; }
QTabBar::tab:selected { background: #29483f; color: #b8f0d8; border-bottom: 2px solid #9edbc1; }
QGroupBox { border: 1px solid #30464c; border-radius: 7px; margin-top: 15px; padding: 13px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; color: #acd6c9; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 12px; height: 12px; border-radius: 2px; border: 1px solid #587178; }
QCheckBox::indicator:unchecked { background: #16262d; }
QCheckBox::indicator:checked { background: #9edbc1; border-color: #bdecd8; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: #111b20; }
QProgressBar { background: #1b2e33; border: none; border-radius: 4px; text-align: center; min-height: 16px; }
QProgressBar::chunk { background: #407c67; border-radius: 4px; }
QToolTip { background: #dfece3; color: #182c25; border: none; }
QSplitter::handle { background: #20343a; width: 2px; }
QStatusBar { background: #17262c; color: #a8bab8; }
QMenuBar, QMenu { background: #17272e; color: #dde7e6; }
QMenu::item:selected { background: #305b51; }
"""


def label(text, kind="muted"):
    widget = QtWidgets.QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    return widget


def button(text, callback, primary=False):
    widget = QtWidgets.QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


def number(value, minimum=0., maximum=1000., decimals=2, suffix=" mm"):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setValue(value)
    widget.setSuffix(suffix)
    return widget


class ProfileDialog(QtWidgets.QDialog):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hardware profile")
        self.setMinimumWidth(500)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(label("Match the hardware you will use", "section"))
        layout.addWidget(label("Start with a prototype preset, then match your actual hardware. Edge and remaining-material allowances are geometry settings, not a load rating."))
        self.presets = QtWidgets.QComboBox()
        self.presets.addItems(["Choose a starting profile…", "10 mm bolt — prototype", "5 mm screw — prototype"])
        layout.addWidget(self.presets)
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit(profile.name)
        form.addRow("Profile name", self.name)
        self.fields = {}
        for key, title in [("shaft_diameter", "Shaft diameter"), ("clearance", "Added diameter clearance"),
                           ("head_diameter", "Head diameter"), ("recess_depth", "Head-seat depth"),
                           ("edge_allowance", "Material outside head seat"), ("floor_allowance", "Material below head seat")]:
            field = number(getattr(profile, key), 0, 100)
            self.fields[key] = field
            form.addRow(title, field)
        self.shape = QtWidgets.QComboBox()
        self.shape.addItems(["counterbore", "countersink"])
        self.shape.setCurrentText(profile.head_shape)
        form.addRow("Head-seat shape", self.shape)
        layout.addLayout(form)
        self.guidance = label("")
        layout.addWidget(self.guidance)
        self.error = label("")
        layout.addWidget(self.error)
        controls = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        controls.accepted.connect(self.validate)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)
        self.save_button = controls.button(QtWidgets.QDialogButtonBox.Save)
        self.presets.currentIndexChanged.connect(self.use_preset)
        for field in self.fields.values():
            field.valueChanged.connect(self.update_guidance)
        self.shape.currentTextChanged.connect(self.update_guidance)
        self.update_guidance()

    def use_preset(self, index):
        if index < 1:
            return
        profile = default_profiles()["bolt" if index == 1 else "screw"]
        self.name.setText(profile.name)
        for key, field in self.fields.items():
            field.setValue(getattr(profile, key))
        self.shape.setCurrentText(profile.head_shape)
        self.update_guidance()

    def update_guidance(self, *_):
        profile = self.profile()
        try:
            profile.validate()
            self.guidance.setText(profile.fit_summary())
            self.error.setText("")
            self.save_button.setEnabled(True)
        except ValueError as error:
            self.guidance.setText("")
            self.error.setText(str(error))
            self.save_button.setEnabled(False)

    def profile(self):
        return HardwareProfile(name=self.name.text(), head_shape=self.shape.currentText(),
                               **{key: field.value() for key, field in self.fields.items()})

    def validate(self):
        try:
            self.profile().validate()
            self.accept()
        except ValueError as error:
            self.error.setText(str(error))


class Worker(QtCore.QObject):
    progress = QtCore.Signal(int, str)
    success = QtCore.Signal(object)
    failure = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.context = TaskContext(self.progress.emit)

    @QtCore.Slot()
    def run(self):
        try:
            result = self.operation(self.context)
            if not self.context.cancelled:
                self.success.emit(result)
            else:
                self.failure.emit("Operation cancelled. Your project is unchanged.")
        except Cancelled:
            self.failure.emit("Operation cancelled. Your project is unchanged.")
        except Exception as error:
            import traceback
            traceback.print_exc()
            self.failure.emit(str(error) or type(error).__name__)
        finally:
            self.finished.emit()
