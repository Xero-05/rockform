from copy import deepcopy
import numpy as np
import trimesh
import manifold3d
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from scipy.spatial import cKDTree
from .geometry import boundary_loops, oriented_patch, validate_solid
from .model import GeometryError, Mount, TaskContext


def _boolean(meshes, operation):
    """Keep double precision across cuts, including narrow triangles around head seats."""
    solids = []
    for mesh in meshes:
        converted = manifold3d.Manifold(manifold3d.Mesh64(
            np.array(mesh.vertices, dtype=np.float64, order="C", copy=True),
            np.array(mesh.faces, dtype=np.uint64, order="C", copy=True)))
        if converted.is_empty():
            raise GeometryError("A cutting shape is not a valid solid. Check the head-seat dimensions.")
        solids.append(converted)
    result = solids[0]
    for solid in solids[1:]:
        result = result + solid if operation == "union" else result - solid
    if result.is_empty():
        raise GeometryError("The mounting cuts remove the entire hold. Reduce the holes or enlarge the shape.")
    output = result.to_mesh64()
    return trimesh.Trimesh(np.array(output.vert_properties)[:, :3].copy(), np.array(output.tri_verts).copy(), process=False)


def footprint(hold):
    patch, protected = oriented_patch(hold)
    loops = boundary_loops(patch)
    if len(loops) != 1:
        raise GeometryError("Rebuild a valid flat-backed hold first.")
    polygon = Polygon(patch.vertices[loops[0], :2])
    if not polygon.is_valid:
        raise GeometryError("The projected hold boundary overlaps. Adjust the backing plane.")
    protected_region = None
    if len(hold.protected_triangles):
        rotation = trimesh.transformations.euler_matrix(np.radians(hold.tilt_x), np.radians(hold.tilt_y), 0)
        triangles = trimesh.transform_points(hold.protected_triangles.reshape(-1, 3) * hold.scale, rotation).reshape(-1, 3, 3)
        polygons = [Polygon(t[:, :2]) for t in triangles]
        protected_region = unary_union([p for p in polygons if p.is_valid and p.area > 1e-8])
    return polygon, patch, (protected, protected_region)


def _top_heights(mesh, xy):
    xy = np.asarray(xy).reshape(-1, 2)
    origins = np.column_stack([xy, np.full(len(xy), mesh.bounds[1, 2] + 10)])
    locations, ray_ids, _ = mesh.ray.intersects_location(origins, np.tile([0., 0., -1.], (len(xy), 1)), multiple_hits=True)
    heights = np.full(len(xy), np.nan)
    counts = np.zeros(len(xy), dtype=int)
    for location, ray_id in zip(locations, ray_ids):
        counts[ray_id] += 1
        heights[ray_id] = max(heights[ray_id], location[2]) if np.isfinite(heights[ray_id]) else location[2]
    return heights, counts


def mount_fit(hold, mount, geometry=None):
    profile = mount.profile
    profile.validate()
    polygon, patch, (protected, protected_region) = geometry or footprint(hold)
    radius = profile.head_diameter / 2 + profile.clearance / 2
    point = Point(mount.x, mount.y)
    if not polygon.contains(point) or polygon.boundary.distance(point) < radius + profile.edge_allowance:
        raise GeometryError("The head seat is too close to the hold boundary. Move the hole inward or adjust the hardware profile.")
    if len(protected) and cKDTree(protected[:, :2]).query([mount.x, mount.y])[0] < radius + profile.edge_allowance:
        raise GeometryError("The mount intersects a protected grip area. Move it or edit the protection mask.")
    if protected_region is not None and protected_region.distance(point) < radius + profile.edge_allowance:
        raise GeometryError("The mount intersects a protected grip area. Move it or edit the protection mask.")
    # Sample the entire recess disk, not just the center, to catch steep/thin surfaces.
    angles = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    samples = [[mount.x, mount.y]]
    for r in (radius * 0.5, radius):
        samples.extend(np.column_stack([mount.x + r * np.cos(angles), mount.y + r * np.sin(angles)]))
    heights, hits = _top_heights(patch, samples)
    if np.isnan(heights).any() or (hits > 1).any():
        raise GeometryError("The mount crosses a gap or undercut. Choose a continuous surface with access from the front.")
    floor = float(heights.min() - profile.recess_depth)
    if floor < profile.floor_allowance:
        raise GeometryError("Too little material remains below the head seat. Move the mount, reduce the recess, or increase backing thickness.")
    return floor, float(heights.max()), radius


def validate_mounts(hold):
    geometry = footprint(hold)
    fits = [mount_fit(hold, m, geometry) for m in hold.mounts]
    for i, a in enumerate(hold.mounts):
        for j, b in enumerate(hold.mounts[:i]):
            required = fits[i][2] + fits[j][2] + max(a.profile.edge_allowance, b.profile.edge_allowance)
            if np.hypot(a.x - b.x, a.y - b.y) < required:
                raise GeometryError("Mount head seats overlap or leave too little material between holes. Move them farther apart.")
    return fits


def generate_mounts(hold, bolt_profile, screw_profile, mode="bolt-on", count=2, anti_rotation=True, context=None):
    ctx = context or TaskContext()
    requests = [("bolt", bolt_profile)] if mode == "bolt-on" else [("screw", screw_profile)] * count
    if mode == "bolt-on" and anti_rotation:
        requests.append(("anti-rotation", screw_profile))
    for _, profile in requests:
        profile.validate()
    geometry = footprint(hold)
    polygon, patch, _ = geometry
    minx, miny, maxx, maxy = polygon.bounds
    xs = np.linspace(minx, maxx, 19)
    ys = np.linspace(miny, maxy, 19)
    center = np.array(polygon.centroid.coords[0])
    candidates = np.array([[x, y] for x in xs for y in ys if polygon.contains(Point(x, y))])
    placed = []
    for number, (role, profile) in enumerate(requests):
        ctx.step(10 + int(number / len(requests) * 70), f"Finding a position for {role}…")
        # Prefer clearance and broad spacing; central positions win for the primary bolt.
        scores = []
        for xy in candidates:
            clearance = polygon.boundary.distance(Point(*xy))
            if placed:
                separation = min(np.linalg.norm(xy - [m.x, m.y]) for m in placed)
                score = separation + clearance * 0.4
            else:
                score = clearance - 0.35 * np.linalg.norm(xy - center)
            scores.append(score)
        reason = "No usable area was found."
        for index in np.argsort(scores)[::-1]:
            ctx.step(10 + int(number / len(requests) * 70), f"Checking {role} clearance and thickness…")
            xy = candidates[index]
            mount = Mount(float(xy[0]), float(xy[1]), deepcopy(profile), role)
            try:
                mount_fit(hold, mount, geometry)
                for previous in placed:
                    required = (profile.head_diameter + previous.profile.head_diameter + profile.clearance + previous.profile.clearance) / 2
                    required += max(profile.edge_allowance, previous.profile.edge_allowance)
                    if np.linalg.norm(xy - [previous.x, previous.y]) < required:
                        raise GeometryError("Not enough separation between the requested holes.")
                placed.append(mount)
                break
            except GeometryError as error:
                reason = str(error)
        else:
            raise GeometryError(f"Could not place {role} {number + 1}: {reason} Try fewer holes, a larger selection, or more backing.")
    ctx.step(100, "Mount layout ready for adjustment")
    return placed


def _cutter(mount, floor, ceiling, radius):
    profile = mount.profile
    bore_radius = profile.bore / 2
    top = ceiling + 3
    shaft = trimesh.creation.cylinder(radius=bore_radius, height=top + 3, sections=64)
    shaft.apply_translation([mount.x, mount.y, (top - 3) / 2])
    if profile.recess_depth <= 0:
        return shaft
    if profile.head_shape == "counterbore":
        seat = trimesh.creation.cylinder(radius=radius, height=top - floor, sections=64)
        seat.apply_translation([mount.x, mount.y, (top + floor) / 2])
    else:
        # Profile radius/height gives the selected conical seat; extend its mouth to clear the surface.
        mouth = floor + profile.recess_depth
        contour = np.array([[0, floor], [bore_radius, floor], [radius, mouth], [radius, top], [0, top]])
        seat = trimesh.creation.revolve(contour, sections=64)
        seat.apply_translation([mount.x, mount.y, 0])
    return _boolean([shaft, seat], "union")


def apply_mounts(hold, context=None):
    ctx = context or TaskContext()
    if hold.solid is None:
        raise GeometryError("Build the hold before applying mounts.")
    fits = validate_mounts(hold)
    ctx.step(30, "Cutting clearance holes and head seats…")
    if not hold.mounts:
        result = hold.solid.copy()
    else:
        cutters = [_cutter(m, *fit) for m, fit in zip(hold.mounts, fits)]
        ctx.step(50, "Creating the printable solid…")
        result = _boolean([hold.solid] + cutters, "difference")
    ctx.step(70, "Validating the resulting hold…")
    validate_solid(result, ctx)
    ctx.step(100, "Printable model ready")
    return result
