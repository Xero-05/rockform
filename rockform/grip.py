"""Conservative broad-feature screening, separate from printability and strength.

Thresholds are prototype product choices in millimeters, not climbing standards.
Only the retained scan is sampled. Backing, edge generation and scan-wide score
normalization cannot turn fine texture into a suggested handhold.
"""
import numpy as np
from scipy.ndimage import gaussian_filter, distance_transform_edt, label
from .geometry import support_width, support_ledge_area
from .model import TaskContext


def assess_grip(hold, up=None, context=None):
    ctx = context or TaskContext()
    ctx.step(50, "Checking retained rock relief and contact area…")
    patch = hold.patch.copy()
    patch.vertices *= hold.scale
    # A physical sampling interval avoids dependence on the number of triangles.
    step = max(2., float(patch.extents[:2].max()) / 120.)
    xs = np.arange(patch.bounds[0, 0] + step / 2, patch.bounds[1, 0], step)
    ys = np.arange(patch.bounds[0, 1] + step / 2, patch.bounds[1, 1], step)
    xx, yy = np.meshgrid(xs, ys)
    xy = np.column_stack([xx.ravel(), yy.ravel()])
    review = dict(criteria_version=1, eligible=False, relief_mm=0., contact_width_mm=0., contact_area_mm2=0.,
                  contact_depth_mm=0., orientation="", scan_up=None if up is None else np.asarray(up).tolist(),
                  support_width_mm=float(support_width(hold)), reasons=[])
    if min(xx.shape, default=0) < 3:
        review['reasons'].append("The retained surface is too small for broad-feature screening.")
        return review
    origins = np.column_stack([xy, np.full(len(xy), patch.bounds[1, 2] + 10.)])
    locations, rays, _ = patch.ray.intersects_location(origins, np.tile([0., 0., -1.], (len(xy), 1)), multiple_hits=True)
    heights = np.full(len(xy), np.nan)
    counts = np.bincount(rays, minlength=len(xy))
    heights[rays] = locations[:, 2]
    valid = (counts == 1).reshape(xx.shape)
    if valid.sum() < 30:
        review['reasons'].append("Too little continuous front-facing scan surface to assess a broad grip.")
        return review
    # Remove overall plane tilt; tilting a flat plate must not invent rock relief.
    fit = np.column_stack([xy[counts == 1], np.ones(valid.sum())])
    plane = np.linalg.lstsq(fit, heights[counts == 1], rcond=None)[0]
    residual = (heights - np.column_stack([xy, np.ones(len(xy))]) @ plane).reshape(xx.shape)
    nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    filled = residual[tuple(nearest)]
    smooth = gaussian_filter(filled, sigma=2. / step)
    # Never count a cut edge or missing-data edge as contact evidence.
    distance = distance_transform_edt(np.pad(valid, 1), sampling=step)[1:-1, 1:-1]
    interior = valid & (distance >= 8.)
    if interior.sum() < 25:
        review['reasons'].append("No broad contact area remains at least 8 mm inside the cut edge.")
        return review
    low, high = np.percentile(smooth[valid], [5, 95])
    review['relief_mm'] = float(high - low)
    if high - low < 8.:
        review['reasons'].append(f"Only {high - low:.1f} mm of broad natural relief remains; suggestions need at least 8 mm. Backing does not count.")
    dy, dx = np.gradient(smooth, step)
    length = np.sqrt(1 + dx * dx + dy * dy)
    local_up = hold.frame[:3, :3] @ np.asarray(up) if up is not None else np.zeros(3)
    if np.linalg.norm(local_up[:2]) >= .35 * max(np.linalg.norm(local_up), 1e-8):
        directions = [local_up[:2] / np.linalg.norm(local_up[:2])]
        review['orientation'] = "Contact screened for the selected scan-up direction; inspect mounting orientation."
    else:
        directions = [np.array([np.cos(a), np.sin(a)]) for a in np.linspace(0, 2 * np.pi, 8, endpoint=False)]
        review['orientation'] = "Scan up is unspecified or points out of the surface. Screening searched possible rotations; set an in-plane scan-up direction to check the intended orientation."
    best = (0., 0., 0.)
    for direction in directions:
        facing_up = -(dx * direction[0] + dy * direction[1]) / length
        mask = interior & (facing_up >= .42) & (1. / length >= .25) & (smooth >= low + 4.)
        groups, count = label(mask)
        for number in range(1, count + 1):
            region = groups == number
            points = np.column_stack([xx[region], yy[region]])
            width = np.ptp(points @ np.array([-direction[1], direction[0]])) + step
            area = region.sum() * step * step
            depth = np.ptp(smooth[region])
            if width >= 20. and area >= 80. and depth >= 6. and area > best[1]:
                best = (float(width), float(area), float(depth))
    review.update(contact_width_mm=best[0], contact_area_mm2=best[1], contact_depth_mm=best[2])
    if best[1] == 0:
        review['reasons'].append("No continuous natural contact slope with 20 mm width, 80 mm² area and 6 mm depth was found away from the cut edge.")
    ctx.step(65, "Checking that generated edges do not dominate the feature…")
    if support_ledge_area(hold) >= 25.:
        review['reasons'].append("The added edge contains an artificial ledge. Rebuild with a gradual wall return or change the selection.")
    # Large skirts indicate the selection cuts through a tall feature instead of
    # returning to low surrounding rock. Do not call that a handhold suggestion.
    if review['support_width_mm'] > min(40., float(patch.extents[:2].min()) * .6):
        review['reasons'].append(f"The cut edge needs a {review['support_width_mm']:.0f} mm wall return, which would dominate this feature. Include surrounding low rock or adjust the backing plane.")
    review['eligible'] = not review['reasons']
    return review


def review_text(hold):
    review = hold.grip_review
    if not review:
        return "This saved shape has not passed the current grip screen. Rebuild to assess it."
    status = "Broad-feature screen passed; inspect the natural contact." if review['eligible'] else "Manual shape only — excluded from automatic handhold suggestions."
    support = (f"Raised feature: {hold.backing:.1f} mm added backing; no surrounding plate." if hold.feature_kind == "protrusion" else
               f"Recess: {hold.backing:.1f} mm below the cavity; {review['support_width_mm']:.1f} mm outside support." if hold.feature_kind == "recess" else
               f"Wall return: {review['support_width_mm']:.1f} mm.")
    return (f"{status}\nNatural relief: {review['relief_mm']:.1f} mm. {support}\n"
            + " ".join(review['reasons']) + "\n" + review['orientation'])
