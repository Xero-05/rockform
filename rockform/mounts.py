from copy import deepcopy
import numpy as np
import trimesh
import manifold3d
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from scipy.spatial import cKDTree
from .geometry import boundary_loops, oriented_patch, validate_solid, support_width
from .model import GeometryError, Mount, TaskContext


class FitError(GeometryError):
    def __init__(self, constraint, message, deficit=0.):
        super().__init__(message)
        self.constraint, self.deficit = constraint, deficit


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
    if hold.solid is None:
        raise GeometryError("Rebuild the hold before placing mounting holes.")
    if hold.edge_style == "projected-base":
        # The original damaged rim may overlap in projection. Mounts use the
        # repaired solid's actual flat base, including any through-openings.
        base = hold.solid.triangles[(np.abs(hold.solid.triangles[:, :, 2]) < 1e-7).all(axis=1)]
        polygon = unary_union([Polygon(t[:, :2]) for t in base])
        if polygon.is_empty or polygon.geom_type != 'Polygon':
            raise GeometryError("The repaired hold has no connected flat base for mounting.")
        body = polygon
        width = 0.
    else:
        loops = boundary_loops(patch)
        if len(loops) != 1:
            raise GeometryError("Rebuild a valid flat-backed hold first.")
        polygon = Polygon(patch.vertices[loops[0], :2])
        body = polygon
        width = support_width(hold, patch, loops[0])
    if not polygon.is_valid:
        raise GeometryError("The projected hold boundary overlaps. Adjust the backing plane.")
    if width > 0:
        polygon = polygon.buffer(width, quad_segs=3)
    # Check the rock AND generated tapered edge, excluding only the flat back.
    top_faces = np.flatnonzero((hold.solid.triangles[:, :, 2] > 1e-7).any(axis=1))
    surface = hold.solid.submesh([top_faces], append=True, repair=False)
    protected_region = None
    if len(hold.protected_triangles):
        rotation = trimesh.transformations.euler_matrix(np.radians(hold.tilt_x), np.radians(hold.tilt_y), 0)
        triangles = trimesh.transform_points(hold.protected_triangles.reshape(-1, 3) * hold.scale, rotation).reshape(-1, 3, 3)
        polygons = [Polygon(t[:, :2]) for t in triangles]
        protected_region = unary_union([p for p in polygons if p.is_valid and p.area > 1e-8])
    return polygon, surface, (protected, protected_region, body)


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
    polygon, patch, (protected, protected_region, body) = geometry or footprint(hold)
    radius = profile.seat_diameter / 2
    point = Point(mount.x, mount.y)
    if hold.edge_style == "wall-ramp" and not body.contains(point):
        raise FitError("boundary", "Move the mount center inside the scanned body's boundary, away from the generated wall return.")
    available = polygon.boundary.distance(point) if polygon.contains(point) else 0.
    required = radius + profile.edge_allowance
    if available < required:
        raise FitError("boundary", f"Too close to the hold boundary: center needs {required:.1f} mm of edge distance; "
                       f"only {available:.1f} mm is available here. Move inward or select a larger feature.", required - available)
    if len(protected) and cKDTree(protected[:, :2]).query([mount.x, mount.y])[0] < radius + profile.edge_allowance:
        raise FitError("protected", "The mount intersects a protected grip area. Move it or edit the protection mask.")
    if protected_region is not None and protected_region.distance(point) < radius + profile.edge_allowance:
        raise FitError("protected", "The mount intersects a protected grip area. Move it or edit the protection mask.")
    # Sample the entire recess disk, not just the center, to catch steep/thin surfaces.
    angles = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    samples = [[mount.x, mount.y]]
    for r in (radius * 0.5, radius):
        samples.extend(np.column_stack([mount.x + r * np.cos(angles), mount.y + r * np.sin(angles)]))
    heights, hits = _top_heights(patch, samples)
    if np.isnan(heights).any() or (hits > 1).any():
        raise FitError("surface", "The mount crosses a gap or undercut. Choose a continuous surface with access from the front.")
    floor = float(heights.min() - profile.recess_depth)
    if floor < profile.floor_allowance:
        deficit = profile.floor_allowance - floor
        raise FitError("thickness", f"Too little material below the head seat: needs {profile.recess_depth + profile.floor_allowance:.1f} mm "
                       f"local thickness ({profile.recess_depth:.1f} mm recess + {profile.floor_allowance:.1f} mm remaining material); "
                       f"only {heights.min():.1f} mm is available here. Move to a thicker area, reduce recess depth for matching hardware, "
                       f"or increase backing by at least {np.ceil(deficit * 10) / 10:.1f} mm and recheck.", deficit)
    # A flared edge narrows above the back. Check material around the seat at
    # the actual seat floor, rather than assuming a vertical side wall.
    if (hold.edge_style == "wall-ramp" or hold.edge_flare > 0) and profile.edge_allowance > 0 and profile.recess_depth > 0:
        outside = np.column_stack([mount.x + required * np.cos(angles), mount.y + required * np.sin(angles)])
        rim_heights, rim_hits = _top_heights(patch, outside)
        if np.isnan(rim_heights).any() or (rim_hits > 1).any() or rim_heights.min() < floor - 1e-5:
            raise FitError("side", f"The tapered side leaves less than {profile.edge_allowance:.1f} mm of material around the seat floor. "
                           "Move the hole farther inward or use a broader feature.")
    return floor, float(heights.max()), radius


def validate_mounts(hold):
    if hold.solid is None:
        raise GeometryError("This feature needs repair before mounting. Repair the scan and re-extract a closed hold.")
    geometry = footprint(hold)
    fits = [mount_fit(hold, m, geometry) for m in hold.mounts]
    for i, a in enumerate(hold.mounts):
        for j, b in enumerate(hold.mounts[:i]):
            required = fits[i][2] + fits[j][2] + max(a.profile.edge_allowance, b.profile.edge_allowance)
            if np.hypot(a.x - b.x, a.y - b.y) < required:
                actual = np.hypot(a.x - b.x, a.y - b.y)
                raise GeometryError(f"Mount head seats overlap or leave too little material: centers need {required:.1f} mm separation; "
                                    f"they are {actual:.1f} mm apart. Move them farther apart.")
    return fits


def generate_mounts(hold, bolt_profile, screw_profile, mode="bolt-on", count=2, anti_rotation=True, context=None):
    if hold.solid is None:
        raise GeometryError("This feature needs repair before mounting. Repair the scan and re-extract a closed hold.")
    ctx = context or TaskContext()
    if mode not in ("bolt-on", "screw-on") or not 1 <= count <= 10:
        raise GeometryError("Choose bolt-on or screw-on and a screw count from 1 to 10.")
    requests = [("bolt", bolt_profile)] if mode == "bolt-on" else [("screw", screw_profile)] * count
    if mode == "bolt-on" and anti_rotation:
        requests.append(("anti-rotation", screw_profile))
    for _, profile in requests:
        profile.validate()
    geometry = footprint(hold)
    polygon, patch, _ = geometry
    placement_region = geometry[2][2] if hold.edge_style == "wall-ramp" else polygon
    minx, miny, maxx, maxy = placement_region.bounds
    xs = np.linspace(minx, maxx, 19)
    ys = np.linspace(miny, maxy, 19)
    center = np.array(placement_region.centroid.coords[0])
    candidates = np.array([*placement_region.representative_point().coords, *([center] if placement_region.contains(Point(center)) else []),
                           *[[x, y] for x in xs for y in ys if placement_region.contains(Point(x, y))]])
    clearances = np.array([polygon.boundary.distance(Point(xy)) for xy in candidates])
    placed, fit_cache, failures = [], {}, []
    budget = 8000

    def search(number):
        nonlocal budget
        if number == len(requests):
            return True
        role, profile = requests[number]
        scores = clearances - .35 * np.linalg.norm(candidates - center, axis=1)
        if placed:
            scores = np.min([np.linalg.norm(candidates - [m.x, m.y], axis=1) for m in placed], axis=0) + .4 * clearances
        branches = 0
        for index in np.argsort(scores)[::-1]:
            budget -= 1
            if budget < 0:
                return False
            ctx.step(10 + int(number / len(requests) * 70), f"Checking {role} clearance and thickness…")
            xy = candidates[index]
            mount = Mount(float(xy[0]), float(xy[1]), profile, role)
            key = (id(profile), int(index))
            if key not in fit_cache:
                try:
                    mount_fit(hold, mount, geometry)
                    fit_cache[key] = None
                except FitError as error:
                    fit_cache[key] = error
            error = fit_cache[key]
            if error is None:
                for previous in placed:
                    required = (profile.seat_diameter + previous.profile.seat_diameter) / 2
                    required += max(profile.edge_allowance, previous.profile.edge_allowance)
                    actual = np.linalg.norm(xy - [previous.x, previous.y])
                    if actual < required:
                        error = FitError("separation", f"Hole centers need {required:.1f} mm separation; "
                                         f"only {actual:.1f} mm is available at the closest-fitting location.", required - actual)
                        break
            if error is not None:
                failures.append((number, error))
                continue
            placed.append(Mount(mount.x, mount.y, deepcopy(profile), role))
            if search(number + 1):
                return True
            placed.pop()
            # Retry different primary holes instead of failing a possible layout
            # simply because a centered bolt blocks the second hole.
            branches += 1
            if branches >= 24:
                return False
        return False

    if not search(0):
        priorities = {"boundary": 0, "protected": 1, "surface": 2, "thickness": 3, "side": 4, "separation": 5}
        number, error = max(failures, key=lambda item: (item[0], priorities[item[1].constraint], -item[1].deficit)) if failures else (0, "No usable area was found.")
        role, profile = requests[number]
        advice = ("Turn off Add anti-rotation screw or choose a broader feature." if role == "anti-rotation" else
                  "Try fewer screws or a broader feature." if mode == "screw-on" else "Try a broader/thicker feature or screw-on mode.")
        raise GeometryError(f"Could not place {role} {number + 1}: {error} {advice}\n{profile.fit_summary()}")
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
