from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
import uuid
import numpy as np
import trimesh


class GeometryError(ValueError):
    """A recoverable geometry problem with a user-facing explanation."""


class Cancelled(Exception):
    pass


class TaskContext:
    def __init__(self, progress: Callable[[int, str], None] | None = None):
        self.cancelled = False
        self.progress = progress or (lambda percent, message: None)

    def step(self, percent: int, message: str):
        if self.cancelled:
            raise Cancelled()
        self.progress(percent, message)


@dataclass
class Revision:
    mesh: trimesh.Trimesh
    label: str = "Original"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    protected: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))

    def __post_init__(self):
        if len(self.protected) != len(self.mesh.vertices):
            self.protected = np.zeros(len(self.mesh.vertices), dtype=bool)


@dataclass
class HardwareProfile:
    name: str = "Enter measured hardware"
    shaft_diameter: float = 0.0
    clearance: float = 0.0
    head_diameter: float = 0.0
    recess_depth: float = 0.0
    head_shape: str = "counterbore"
    edge_allowance: float = 3.0
    floor_allowance: float = 3.0

    @property
    def bore(self):
        return self.shaft_diameter + self.clearance

    def validate(self):
        values = [self.shaft_diameter, self.clearance, self.head_diameter,
                  self.recess_depth, self.edge_allowance, self.floor_allowance]
        if not np.isfinite(values).all():
            raise GeometryError("Hardware dimensions must be finite numbers.")
        if self.shaft_diameter <= 0 or self.clearance < 0:
            raise GeometryError("Enter the measured shaft diameter and nonnegative clearance.")
        if self.head_diameter < self.bore or self.recess_depth < 0:
            raise GeometryError("The head seat must be at least as wide as the clearance hole.")
        if self.edge_allowance < 0 or self.floor_allowance <= 0:
            raise GeometryError("Enter a nonnegative edge allowance and positive material allowance.")
        if self.head_shape not in ("counterbore", "countersink"):
            raise GeometryError("Choose counterbore or countersink.")


@dataclass
class Mount:
    x: float
    y: float
    profile: HardwareProfile
    role: str = "bolt"
    manual: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Hold:
    name: str
    revision_id: str
    face_indices: np.ndarray
    patch: trimesh.Trimesh
    protected_points: np.ndarray
    frame: np.ndarray
    protected_triangles: np.ndarray = field(default_factory=lambda: np.empty((0, 3, 3)))
    backing: float = 10.0
    scale: float = 1.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    mounts: list[Mount] = field(default_factory=list)
    mode: str = "bolt-on"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    solid: trimesh.Trimesh | None = None
    result: trimesh.Trimesh | None = None
    preparation: str = "Original selected surface."


@dataclass
class Project:
    original: trimesh.Trimesh
    source_name: str
    revisions: list[Revision] = field(default_factory=list)
    cursor: int = 0
    holds: list[Hold] = field(default_factory=list)
    profiles: dict[str, HardwareProfile] = field(default_factory=lambda: {
        "bolt": HardwareProfile(name="Bolt — measured dimensions"),
        "screw": HardwareProfile(name="Screw — measured dimensions", head_shape="countersink"),
    })
    up: np.ndarray = field(default_factory=lambda: np.array([0., 1., 0.]))
    scale_factor: float = 1.0
    archived_revisions: list[Revision] = field(default_factory=list)
    source_units: str = ""

    def __post_init__(self):
        if not self.revisions:
            self.revisions = [Revision(self.original.copy())]

    @property
    def current(self):
        return self.revisions[self.cursor]

    def commit(self, revision: Revision):
        # Keep redo revisions referenced by holds, even when branching history.
        retained = {h.revision_id for h in self.holds}
        future = self.revisions[self.cursor + 1:]
        history = self.revisions[:self.cursor + 1]
        orphaned = [r for r in future if r.id in retained]
        self.archived_revisions.extend(orphaned)
        self.revisions = history + [revision]
        self.cursor = len(self.revisions) - 1

    def reset(self):
        self.commit(Revision(self.original.copy(), "Reset to original"))

    def undo(self):
        self.cursor = max(0, self.cursor - 1)

    def redo(self):
        self.cursor = min(len(self.revisions) - 1, self.cursor + 1)
