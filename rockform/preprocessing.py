"""Conservative import decimation and explicit, reversible scan cropping."""
from dataclasses import dataclass
from copy import deepcopy
import numpy as np
import trimesh
import pyvista as pv
from scipy.spatial import cKDTree
from vtkmodules.vtkFiltersCore import vtkDecimatePro
from .model import Revision, GeometryError, TaskContext
from .cleanup import CleanupPreview


@dataclass
class DecimationOptions:
    tolerance_mm: float = .2
    dense_edge_mm: float = 3.
    neighborhood_mm: float = 6.
    feature_angle: float = 12.
    reduction: float = .75


def _boundary_edges(faces):
    edges = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    edges, counts = np.unique(edges, axis=0, return_counts=True)
    return edges[counts != 2]


def _subset(revision, faces, label, processing, crop_boundary=None):
    used, inverse = np.unique(faces, return_inverse=True)
    mesh = trimesh.Trimesh(revision.mesh.vertices[used].copy(), inverse.reshape(-1, 3), process=False)
    return Revision(mesh, label, protected=revision.protected[used].copy(),
                    crop_boundary=(revision.crop_boundary if crop_boundary is None else crop_boundary)[used].copy(),
                    processing={**deepcopy(revision.processing), **processing})


def _low_detail_faces(mesh, options, ctx):
    """Constrain reduction to dense, locally planar cells at two grid offsets.

    Both normal variation and departure from a local plane are measured. This
    keeps a finely tessellated curved feature from passing just because each
    individual triangle has a small dihedral angle. No wall plane is required.
    """
    triangles, centers, normals = mesh.triangles, mesh.triangles_center, mesh.face_normals
    edges = triangles - np.roll(triangles, 1, axis=1)
    eligible = (np.linalg.norm(edges, axis=2).max(axis=1) <= options.dense_edge_mm)
    eligible &= mesh.area_faces > 1e-10
    cosine = np.cos(np.radians(options.feature_angle))
    for pass_id, shift in enumerate((0., .5)):
        ctx.step(10 + pass_id * 10, "Finding dense areas with little surface detail…")
        cells = np.floor(centers / options.neighborhood_mm + shift).astype(np.int64)
        _, first, groups = np.unique(cells, axis=0, return_index=True, return_inverse=True)
        n = len(first)
        weights = mesh.area_faces
        totals = np.bincount(groups, weights=weights, minlength=n)
        local_center = np.column_stack([np.bincount(groups, weights=weights * centers[:, i], minlength=n)
                                       for i in range(3)]) / np.maximum(totals[:, None], 1e-15)
        # Opposite winding alone should not make a geometrically flat region rough.
        signed = normals * np.where((normals * normals[first[groups]]).sum(axis=1) < 0, -1., 1.)[:, None]
        mean = np.column_stack([np.bincount(groups, weights=weights * signed[:, i], minlength=n) for i in range(3)])
        mean /= np.maximum(np.linalg.norm(mean, axis=1)[:, None], 1e-15)
        alignment = np.abs((normals * mean[groups]).sum(axis=1))
        min_alignment = np.ones(n)
        np.minimum.at(min_alignment, groups, alignment)
        departures = np.abs(np.einsum('fvi,fi->fv', triangles - local_center[groups, None, :], mean[groups])).max(axis=1)
        max_departure = np.zeros(n)
        np.maximum.at(max_departure, groups, departures)
        flat = (min_alignment >= cosine) & (max_departure <= options.tolerance_mm)
        eligible &= flat[groups]
    return eligible


def _probe_error(original, reduced, ctx):
    """Bidirectional deterministic surface samples; not a Hausdorff proof."""
    maximum = 0.
    for i, (source, target) in enumerate(((original, reduced), (reduced, original))):
        ctx.step(70 + i * 10, "Checking the simplified surface against the scan…")
        ids = np.linspace(0, len(source.faces) - 1, min(4000, len(source.faces))).astype(int)
        triangles = source.triangles[ids]
        probes = np.vstack([triangles.reshape(-1, 3), triangles.mean(axis=1),
                            ((triangles + np.roll(triangles, 1, axis=1)) / 2).reshape(-1, 3)])
        for start in range(0, len(probes), 4000):
            ctx.step(70 + i * 10, "Checking the simplified surface against the scan…")
            distance = trimesh.proximity.closest_point(target, probes[start:start + 4000])[1]
            maximum = max(maximum, float(distance.max()))
    return maximum


def adaptive_decimate(revision, options=None, context=None):
    options = options or DecimationOptions()
    ctx = context or TaskContext()
    values = [options.tolerance_mm, options.dense_edge_mm, options.neighborhood_mm, options.feature_angle, options.reduction]
    if not np.isfinite(values).all() or min(values[:3]) <= 0 or not 0 < options.feature_angle < 90 or not 0 < options.reduction < 1:
        raise GeometryError("Enter positive decimation distances, an angle below 90°, and a reduction between 0 and 1.")
    ctx.step(5, "Inspecting scan density without repairing the scan…")
    mesh = revision.mesh
    eligible = _low_detail_faces(mesh, options, ctx)
    pinned = revision.protected | revision.crop_boundary
    # Keep every face touching a boundary, nonmanifold edge or protected vertex.
    pinned = pinned.copy()
    pinned[np.unique(_boundary_edges(mesh.faces))] = True
    eligible &= ~pinned[mesh.faces].any(axis=1)
    processing = dict(operation="adaptive-decimation", source_revision=revision.id,
                      tolerance_mm=options.tolerance_mm, dense_edge_mm=options.dense_edge_mm,
                      eligible_faces=int(eligible.sum()), input_faces=len(mesh.faces), sampled_error_mm=0.)

    def unchanged(reason):
        processing.update(output_faces=len(mesh.faces), reduction_applied=False, summary=reason)
        result = _subset(revision, mesh.faces, "Import density unchanged", processing)
        ctx.step(100, reason)
        return CleanupPreview(result, None, reason + " Original retained; no scan repair performed.")

    if eligible.sum() < 32:
        return unchanged("No dense, low-detail area suitable for automatic decimation was found.")
    used, faces = np.unique(mesh.faces[eligible], return_inverse=True)
    part = trimesh.Trimesh(mesh.vertices[used], faces.reshape(-1, 3), process=False)
    poly = pv.PolyData(part.vertices, np.column_stack([np.full(len(part.faces), 3), part.faces]).ravel())
    poly.point_data['source_vertex'] = used
    ctx.step(40, "Decimating low-detail areas; keeping feature and patch boundaries…")
    decimator = vtkDecimatePro()
    decimator.SetInputData(poly)
    decimator.SetPreserveTopology(True)
    decimator.SetSplitting(False)
    decimator.SetBoundaryVertexDeletion(False)
    decimator.SetFeatureAngle(options.feature_angle)
    decimator.SetErrorIsAbsolute(True)
    decimator.SetAbsoluteError(options.tolerance_mm ** 2)
    decimator.SetAccumulateError(True)
    boundary = _boundary_edges(mesh.faces[eligible])
    for strength in (options.reduction, options.reduction * 2 / 3, options.reduction / 3):
        ctx.step(40, "Reducing density within the surface-deviation limit…")
        decimator.SetTargetReduction(strength)
        decimator.Update()
        ctx.step(65, "Checking preserved boundaries…")
        output = pv.wrap(decimator.GetOutput())
        if not output.n_cells or output.n_cells >= len(part.faces):
            return unchanged("The existing boundaries leave no useful automatic reduction.")
        source_ids = np.asarray(output.point_data['source_vertex'], dtype=int)
        if not np.array_equal(output.points, mesh.vertices[source_ids]):
            return unchanged("Automatic reduction was skipped because it moved source vertices.")
        reduced_faces = source_ids[output.faces.reshape(-1, 4)[:, 1:]]
        if not np.array_equal(boundary, _boundary_edges(reduced_faces)):
            return unchanged("Automatic reduction was skipped because a patch boundary changed.")
        reduced = trimesh.Trimesh(mesh.vertices, reduced_faces, process=False)
        reduced.remove_unreferenced_vertices()
        error = _probe_error(part, reduced, ctx)
        if error <= options.tolerance_mm + 1e-7:
            processing['target_reduction'] = strength
            break
    else:
        return unchanged("Automatic reduction was skipped because the surface deviation exceeded its limit.")
    final_faces = np.vstack([mesh.faces[~eligible], reduced_faces])
    summary = (f"Adaptive decimation: {len(mesh.faces):,} → {len(final_faces):,} triangles. "
               f"Dense, low-detail areas reduced; sampled deviation {error:.3f} mm "
               f"(limit {options.tolerance_mm:.2f} mm). No smoothing or gap filling. Undo restores the previous scan; Reset restores the original.")
    processing.update(output_faces=len(final_faces), reduction_applied=True, sampled_error_mm=error, summary=summary)
    result = _subset(revision, final_faces, "Adaptive decimation", processing)
    ctx.step(100, "Import preprocessing complete — select your area of interest")
    return CleanupPreview(result, None, summary)


def crop_interest(revision, selected_faces, margin_mm=20., context=None):
    """Retain whole scanned triangles near the painted area, without repairing."""
    ctx = context or TaskContext()
    ctx.step(10, "Preparing area-of-interest preview…")
    mesh = revision.mesh
    selected = np.asarray(selected_faces, dtype=bool)
    if selected.shape != (len(mesh.faces),) or not selected.any():
        raise GeometryError("Paint an Area of interest on the working scan first.")
    if not np.isfinite(margin_mm) or margin_mm < 0:
        raise GeometryError("The surrounding-wall margin must be a nonnegative distance in millimeters.")
    keep = selected.copy()
    if margin_mm > 0:
        seed_points = mesh.vertices[np.unique(mesh.faces[selected])]
        distances = cKDTree(seed_points).query(mesh.triangles_center)[0]
        keep |= distances <= margin_mm
    if keep.all():
        raise GeometryError("This area and margin include the entire scan. Paint a smaller area or reduce the margin, or repair the whole scan.")
    if keep.sum() < 4:
        raise GeometryError("Keep a larger area, including wall around the feature.")
    ctx.step(55, "Excluding surrounding geometry and preserving the crop edge…")
    cut = revision.crop_boundary.copy()
    # Only edges exposed by this crop are new; existing scan holes remain repairable.
    original_edges = {tuple(e) for e in _boundary_edges(mesh.faces)}
    new_edges = [e for e in _boundary_edges(mesh.faces[keep]) if tuple(e) not in original_edges]
    if new_edges:
        cut[np.unique(new_edges)] = True
    summary = (f"Area of interest: keep {keep.sum():,} of {len(keep):,} triangles, with {margin_mm:g} mm surrounding-wall margin. "
               "Coral geometry is excluded. Apply this area before repair. Crop edges stay open; Undo restores the previous scan.")
    result = _subset(revision, mesh.faces[keep], "Area of interest", dict(operation="area-of-interest",
        source_revision=revision.id, margin_mm=margin_mm, summary=summary,
        area_of_interest=dict(source_revision=revision.id, margin_mm=margin_mm)), cut)
    removed = mesh.submesh([np.flatnonzero(~keep)], append=True, repair=False)
    ctx.step(100, "Area preview ready — no repair has been performed")
    return CleanupPreview(result, removed, summary)
