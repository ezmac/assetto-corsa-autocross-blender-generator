"""
image_recognition.py — Autocross cone detection from overhead map images.

Detects two cone types from map images:
  - Standing cones  (squares/circles in the map)  -> placed upright in Blender
  - Pointer cones   (triangles/arrows in the map)  -> placed lying flat in Blender

Usage (standalone):
    python image_recognition.py Seneca_Grand_Prix_2021.jpg --output cones.json
    python image_recognition.py map.jpg --scale 0.179 --debug

Usage (as module):
    from image_recognition import detect_cones_from_map
    result = detect_cones_from_map('map.jpg')
    # result['standing'] = [{'x':..., 'y':...}, ...]
    # result['pointers'] = [{'x':..., 'y':..., 'heading_deg':...}, ...]
    # result['scale']    = meters per pixel
    # result['center']   = (cx_px, cy_px) image center used for conversion

Dependencies: pillow, scipy, numpy, scikit-learn  (pip install pillow scipy numpy scikit-learn)
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.spatial import ConvexHull

# scikit-learn is optional — only needed when --classifier is used
try:
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False


# ---------------------------------------------------------------------------
# Scale detection
# ---------------------------------------------------------------------------

def detect_grid_scale(arr, grid_feet=20.0, debug=False):
    """
    Auto-detect the m/px scale by finding the grid line spacing.

    The map background has a faint coloured grid (often light blue) whose
    spacing is declared in the legend (e.g. "20' squares").  We find
    nearly-full-width rows of those grid pixels and measure their spacing.

    Returns scale in metres/pixel, or None if not found.
    """
    feet_to_m = 0.3048
    grid_m = grid_feet * feet_to_m   # 20' = 6.096 m

    # Light-blue grid lines: blue channel notably above red, both fairly bright
    blue_mask = (
        (arr[:, :, 2].astype(int) - arr[:, :, 0].astype(int) > 15) &
        (arr[:, :, 2] > 180) &
        (arr[:, :, 0] > 150)
    )
    W = arr.shape[1]
    blue_per_row = blue_mask.sum(axis=1)

    # Rows where >=60 % of pixels are grid-blue -> horizontal grid line
    full_rows = np.where(blue_per_row > W * 0.6)[0]
    if len(full_rows) < 2:
        return None

    spacings = np.diff(full_rows)
    # Only spacings in the plausible single-square range
    consistent = [int(s) for s in spacings if 20 < s < 80]
    if not consistent:
        return None

    # Use median to ignore doubled gaps (missed grid lines) that inflate the mean
    consistent.sort()
    median_sp = consistent[len(consistent) // 2]
    # Keep only spacings within 30 % of the median (single-square spacings)
    single = [s for s in consistent if s <= median_sp * 1.3]
    px_per_square = sum(single) / len(single)
    scale = grid_m / px_per_square
    print(f"Grid detected: {px_per_square:.1f} px = {grid_feet:.0f}ft "
          f"-> scale = {scale:.4f} m/px")
    return scale


# ---------------------------------------------------------------------------
# Orange pixel detection helpers
# ---------------------------------------------------------------------------

ORANGE_MASK_PARAMS = dict(
    r_min=180, g_min=80, g_max=200, b_max=110, rg_margin=50
)

# Use this for retouched images where cone markers are solid red
RED_MASK_PARAMS = dict(
    r_min=200, g_min=0, g_max=70, b_max=30, rg_margin=150
)


def orange_mask(arr, params=None):
    """Return boolean mask of orange pixels."""
    p = params or ORANGE_MASK_PARAMS
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return (
        (r > p['r_min']) &
        (g > p['g_min']) & (g < p['g_max']) &
        (b < p['b_max']) &
        (r.astype(int) - g.astype(int) > p['rg_margin'])
    )


def quantize_orange_mask(img, n_colors=32, params=None, debug=False, debug_dir=None):
    """
    Quantize the image to N palette colors, then return a boolean mask of
    pixels whose palette entry passes the orange criteria.

    This is more robust than per-pixel thresholding when orange has slight
    JPEG compression variance: all similar orange pixels map to the same
    cluster center, making the mask exact rather than approximate.

    Returns the boolean mask, or None if no orange palette entry was found
    (caller should fall back to orange_mask).
    """
    p = params or ORANGE_MASK_PARAMS

    # Quantize (MEDIANCUT = 0 in all Pillow versions)
    q = img.quantize(colors=n_colors, method=0)
    palette = q.getpalette()   # flat [R0,G0,B0, R1,G1,B1, ...]

    orange_indices = []
    print(f"Quantize palette ({n_colors} colors) — orange entries:")
    for i in range(n_colors):
        r, g, b = palette[3 * i], palette[3 * i + 1], palette[3 * i + 2]
        is_orange = (
            r > p['r_min'] and
            p['g_min'] < g < p['g_max'] and
            b < p['b_max'] and
            r - g > p['rg_margin']
        )
        if is_orange:
            orange_indices.append(i)
            print(f"  [{i:2d}] RGB({r:3d},{g:3d},{b:3d}) <-- orange")

    if not orange_indices:
        print("WARNING: quantize found no orange palette entries; "
              "falling back to per-pixel thresholding.")
        return None

    # Build boolean mask
    q_arr = np.array(q)   # 2D uint8 array of palette indices
    mask = np.zeros(q_arr.shape, dtype=bool)
    for idx in orange_indices:
        mask |= (q_arr == idx)

    # Optional: save a palette swatch strip for visual inspection
    if debug and debug_dir:
        _save_palette_strip(palette, n_colors, orange_indices, debug_dir)

    return mask


def _save_palette_strip(palette, n_colors, orange_indices, out_dir):
    """Save a small palette swatch image for debugging the quantize step."""
    sw = 24   # swatch width/height in pixels
    strip = Image.new('RGB', (sw * n_colors, sw * 2))
    draw = ImageDraw.Draw(strip)
    for i in range(n_colors):
        r, g, b = palette[3 * i], palette[3 * i + 1], palette[3 * i + 2]
        draw.rectangle([i * sw, 0, (i + 1) * sw - 1, sw - 1], fill=(r, g, b))
        if i in orange_indices:
            draw.rectangle(
                [i * sw, sw, (i + 1) * sw - 1, sw * 2 - 1],
                fill=(255, 0, 0)
            )
    out_path = Path(out_dir) / 'debug_palette.png'
    strip.save(str(out_path))
    print(f"Palette strip saved: {out_path}")


# ---------------------------------------------------------------------------
# Two-stage clustering
# ---------------------------------------------------------------------------

def greedy_merge(points, weights, radius):
    """
    Merge points within `radius` of each other using weighted centroid.
    Returns list of (cx, cy, total_weight) for each merged group.
    NOTE: This is a single-pass greedy merge — each point is assigned to
    the first earlier point within radius.  Use small radii to avoid
    accidentally merging distinct nearby cones.
    """
    used = [False] * len(points)
    groups = []
    for i in range(len(points)):
        if used[i]:
            continue
        gp = [points[i]]
        gw = [weights[i]]
        used[i] = True
        for j in range(i + 1, len(points)):
            if not used[j]:
                d = math.hypot(points[i][0] - points[j][0],
                               points[i][1] - points[j][1])
                if d < radius:
                    gp.append(points[j])
                    gw.append(weights[j])
                    used[j] = True
        gp_arr = np.array(gp)
        gw_arr = np.array(gw, dtype=float)
        cx = float((gp_arr[:, 0] * gw_arr).sum() / gw_arr.sum())
        cy = float((gp_arr[:, 1] * gw_arr).sum() / gw_arr.sum())
        groups.append((cx, cy, float(gw_arr.sum())))
    return groups


def _rdp_simplify(pts, epsilon):
    """Ramer-Douglas-Peucker polyline simplification (operates on numpy rows)."""
    if len(pts) <= 2:
        return list(pts)
    p0, p1 = pts[0], pts[-1]
    line = p1 - p0
    line_len = float(np.linalg.norm(line))
    d_max, idx = 0.0, 1
    for i in range(1, len(pts) - 1):
        if line_len < 1e-9:
            d = float(np.linalg.norm(pts[i] - p0))
        else:
            d = abs(float(np.cross(line, pts[i] - p0))) / line_len
        if d > d_max:
            d_max, idx = d, i
    if d_max > epsilon:
        left = _rdp_simplify(pts[:idx + 1], epsilon)
        right = _rdp_simplify(pts[idx:], epsilon)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def _count_hull_corners(hull_pts, epsilon=1.5):
    """
    Simplify the convex hull polygon with RDP and return the vertex count.
    A triangle simplifies to ~3, a square to ~4.
    epsilon=1.5 works well for blobs in the 5-40 px size range.
    """
    if len(hull_pts) < 3:
        return len(hull_pts)
    closed = np.vstack([hull_pts, hull_pts[0]])
    simplified = _rdp_simplify(closed, epsilon)
    return max(len(simplified) - 1, 1)  # -1 because start == end in closed polygon


def classify_blob(mask_region, cx_abs, cy_abs):
    """
    Given the orange pixel mask in a local window and the absolute centroid,
    return a dict with shape metrics used for classification.

    Isolates the single connected component in the window that is closest to
    the window centre before computing any metrics.  This prevents multi-blob
    windows (where several nearby cones share a window) from producing
    misleadingly low fill_ratio values.

    Returns: {'aspect': float, 'max_dim': int, 'w': int, 'h': int,
              'pixel_count': int, 'min_hull_angle': float,
              'fill_ratio': float, 'hull_vertex_count': int}

    fill_ratio uses (w+1)*(h+1) bbox to avoid off-by-one with pixel spans.
      ~0.77 for a solid square, ~0.39-0.50 for a solid triangle.

    hull_vertex_count is the RDP-simplified vertex count of the convex hull.
      ~3 for a triangle, ~4 for a square (epsilon=1.5 px).

    min_hull_angle is the sharpest interior angle at any convex hull vertex.
      A triangle tip gives ~30-55 deg; a square corner gives ~90 deg.
    """
    # ---- Window-based metrics (used by all existing classification rules) ----
    # Uses ALL orange pixels in the window so that multi-blob windows produce
    # naturally low fill_ratio values, which the is_pair / is_sparse_pointer
    # rules depend on.
    ys, xs = np.where(mask_region)
    if len(xs) < 3:
        return None
    w = int(xs.max() - xs.min())
    h = int(ys.max() - ys.min())
    aspect = max(w, h) / max(min(w, h), 1)

    min_hull_angle = 180.0
    if len(xs) >= 4:
        try:
            pts = np.column_stack([xs, ys]).astype(float)
            hull = ConvexHull(pts)
            hull_pts = pts[hull.vertices]
            n = len(hull_pts)
            for i in range(n):
                p = hull_pts[i]
                a = hull_pts[(i - 1) % n]
                b = hull_pts[(i + 1) % n]
                v1 = a - p; v2 = b - p
                denom = np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9
                cos_a = np.dot(v1, v2) / denom
                angle_deg = math.degrees(math.acos(float(np.clip(cos_a, -1, 1))))
                if angle_deg < min_hull_angle:
                    min_hull_angle = angle_deg
        except Exception:
            pass

    # Corrected fill ratio: (w+1)*(h+1) avoids off-by-one with pixel coordinate spans.
    fill_ratio = len(xs) / float((w + 1) * (h + 1))

    # ---- Isolated-component metrics (used only for hull vertex count rule) ----
    # Find the connected component nearest to the window centre and compute
    # its hull vertex count.  A clean triangle simplifies to ~3 vertices;
    # a clean square simplifies to ~4.  Meaningful only for single-cone blobs;
    # multi-blob windows land on whichever component is closest to centre.
    iso_hull_vertex_count = 0
    iso_max_dim = 0
    try:
        labeled_w, n_w = ndimage.label(mask_region)
        if n_w > 0:
            win_cy, win_cx = mask_region.shape[0] / 2.0, mask_region.shape[1] / 2.0
            best_lbl, best_d = 1, 1e9
            for lbl in range(1, n_w + 1):
                cy_c = float(np.where(labeled_w == lbl)[0].mean())
                cx_c = float(np.where(labeled_w == lbl)[1].mean())
                d = math.hypot(cx_c - win_cx, cy_c - win_cy)
                if d < best_d:
                    best_d, best_lbl = d, lbl
            iso_ys, iso_xs = np.where(labeled_w == best_lbl)
            iso_max_dim = int(max(iso_xs.max()-iso_xs.min(), iso_ys.max()-iso_ys.min()))
            if len(iso_xs) >= 4:
                iso_pts = np.column_stack([iso_xs, iso_ys]).astype(float)
                iso_hull = ConvexHull(iso_pts)
                iso_hull_pts = iso_pts[iso_hull.vertices]
                iso_hull_vertex_count = _count_hull_corners(iso_hull_pts, epsilon=1.5)
    except Exception:
        pass

    return {
        'aspect': aspect,
        'max_dim': max(w, h),
        'w': w, 'h': h,
        'pixel_count': len(xs),
        'min_hull_angle': min_hull_angle,
        'fill_ratio': fill_ratio,
        'hull_vertex_count': iso_hull_vertex_count,   # isolated-component hull
        'iso_max_dim': iso_max_dim,                   # isolated-component max dim
    }


# ---------------------------------------------------------------------------
# Tip direction from convex hull
# ---------------------------------------------------------------------------

def tip_direction_from_hull(mask, x0, y0):
    """
    Find the pointing direction of a triangular/arrow blob using its convex hull.

    Strategy:
      1. Compute convex hull of orange pixels in the blob window.
      2. For each hull vertex, measure the interior angle at that vertex.
      3. The vertex with the SMALLEST interior angle is the tip (sharpest point).
      4. Return the direction from centroid -> tip as an angle in degrees
         (0° = +X image direction, 90° = +Y-down image direction).

    Falls back to inertia-tensor axis + fewer-pixel-half heuristic if hull fails.
    """
    ys, xs = np.where(mask)
    if len(xs) < 4:
        return _tip_direction_inertia(xs, ys)

    pts = np.column_stack([xs, ys]).astype(float)
    cx = pts[:, 0].mean()
    cy = pts[:, 1].mean()

    try:
        hull = ConvexHull(pts)
        hull_pts = pts[hull.vertices]

        # Compute interior angle at each hull vertex
        n = len(hull_pts)
        min_angle = 360.0
        tip_x, tip_y = hull_pts[0]
        for i in range(n):
            p = hull_pts[i]
            a = hull_pts[(i - 1) % n]
            b = hull_pts[(i + 1) % n]
            v1 = a - p;  v2 = b - p
            cos_a = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
            angle_deg = math.degrees(math.acos(float(np.clip(cos_a, -1, 1))))
            if angle_deg < min_angle:
                min_angle = angle_deg
                tip_x, tip_y = p

        heading = math.degrees(math.atan2(tip_y - cy, tip_x - cx))
        return heading

    except Exception:
        return _tip_direction_inertia(xs, ys)


def _tip_direction_inertia(xs, ys):
    """Fallback: inertia tensor axis + fewer-pixel-half heuristic."""
    cx = xs.mean();  cy = ys.mean()
    xs_c = xs - cx;  ys_c = ys - cy
    Ixx = float((xs_c ** 2).mean())
    Iyy = float((ys_c ** 2).mean())
    Ixy = float((xs_c * ys_c).mean())
    axis_deg = math.degrees(0.5 * math.atan2(2 * Ixy, Ixx - Iyy))
    cos_a = math.cos(math.radians(axis_deg))
    sin_a = math.sin(math.radians(axis_deg))
    proj = xs_c * cos_a + ys_c * sin_a
    half_pos = (proj > 0).sum()
    half_neg = (proj <= 0).sum()
    # Fewer pixels on positive-axis side -> tip is there
    if half_pos < half_neg:
        return axis_deg
    else:
        return axis_deg + 180.0


# ---------------------------------------------------------------------------
# SVM-based classifier (optional, requires scikit-learn)
# ---------------------------------------------------------------------------

_PATCH_SIZE = 15   # pixels (must match between training and inference)


def _extract_patch(omask, cx, cy):
    """
    Extract a flattened _PATCH_SIZE x _PATCH_SIZE boolean patch from the
    orange mask centred on pixel (cx, cy).  Edge padding with zeros.
    """
    H, W = omask.shape
    r = _PATCH_SIZE // 2
    x0 = max(0, int(cx) - r)
    y0 = max(0, int(cy) - r)
    x1 = x0 + _PATCH_SIZE
    y1 = y0 + _PATCH_SIZE
    # Clip to image bounds and pad
    if x1 > W or y1 > H:
        patch = np.zeros((_PATCH_SIZE, _PATCH_SIZE), dtype=np.float32)
        src = omask[y0:min(H, y1), x0:min(W, x1)].astype(np.float32)
        patch[:src.shape[0], :src.shape[1]] = src
    else:
        patch = omask[y0:y1, x0:x1].astype(np.float32)
    return patch.flatten()


def _train_classifier(omask, labels_path):
    """
    Load labels from *labels_path*, extract mask patches, and train an SVM.

    Returns a fitted sklearn Pipeline (StandardScaler → SVC), or None if
    scikit-learn is not installed or the labels file cannot be read.

    Label file format (produced by label_cones.py):
        {"image": "...", "labels": [{"x": int, "y": int, "type": "s"|"p"}, ...]}

    'type' == 's' → class 0 (standing), 'p' → class 1 (pointer).
    """
    if not _SKLEARN_OK:
        print("WARNING: scikit-learn not found; --classifier ignored. "
              "Install with: pip install scikit-learn")
        return None

    labels_path = Path(labels_path)
    if not labels_path.exists():
        print(f"WARNING: labels file not found: {labels_path}; "
              "--classifier ignored.")
        return None

    with open(labels_path) as f:
        data = json.load(f)
    label_list = data.get('labels', [])
    if len(label_list) < 4:
        print(f"WARNING: only {len(label_list)} labels found; "
              "need at least 4 to train. --classifier ignored.")
        return None

    features, targets = [], []
    for lbl in label_list:
        feat = _extract_patch(omask, lbl['x'], lbl['y'])
        features.append(feat)
        targets.append(0 if lbl['type'] == 's' else 1)

    X = np.array(features)
    y = np.array(targets)

    n_s = int((y == 0).sum())
    n_p = int((y == 1).sum())
    if n_s == 0 or n_p == 0:
        print(f"WARNING: labels have only one class (s={n_s}, p={n_p}); "
              "--classifier ignored.")
        return None

    clf = Pipeline([
        ('scaler', StandardScaler()),
        ('svm', SVC(kernel='rbf', C=10.0, gamma='scale', class_weight='balanced')),
    ])
    clf.fit(X, y)
    print(f"SVM classifier trained: {n_s} standing + {n_p} pointer examples  "
          f"(patch {_PATCH_SIZE}x{_PATCH_SIZE})")
    return clf


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------

def detect_cones_from_map(
    image_path,
    scale_m_per_px=None,
    grid_feet=20.0,
    standing_merge_px=12,
    pointer_merge_px=12,
    mask_params=None,
    center_pixel=None,
    pointer_aspect_thresh=2.5,
    pointer_min_dim_px=15,
    tip_angle_thresh=65.0,
    pair_min_aspect=1.1,
    standing_fill_min=0.65,
    pointer_fill_max=0.55,
    pairing_add_standing_threshold_m=20.0,
    window_px=35,
    min_component_px=3,
    max_component_px=200,
    use_quantize=False,
    quantize_colors=32,
    classifier_labels=None,
    debug=False,
    debug_dir=None,
):
    """
    Detect standing and pointer cones from an autocross overhead map image.

    Parameters
    ----------
    image_path : str | Path
        Path to the map image (JPEG, PNG, BMP …).
    scale_m_per_px : float | None
        Metres per pixel.  If None, auto-detected from the grid.
    grid_feet : float
        Size of the grid squares declared in the map legend (default 20 ft).
    standing_merge_px : int
        Merge radius (px) for grouping standing-cone sub-clusters.
        Small (~12 px) keeps adjacent standing + pointer cones separate.
    pointer_merge_px : int
        Merge radius (px) for grouping pointer-cone fragments.
        Keep small (same as standing_merge_px) — pointer symbols in this
        map are single blobs, not fragmented, so a large radius wrongly
        merges adjacent pointer cones into one.
    pointer_aspect_thresh : float
        Minimum aspect ratio for a blob to be classified as a lone pointer.
        Lowered to 2.5 because pointer triangles in this map are ~17px tall x
        5px wide (aspect ~3.4), not the 4:1 ratio assumed earlier.
    pointer_min_dim_px : int
        Minimum length of longest side (px) for a pointer or pair classification.
    tip_angle_thresh : float
        Max interior convex hull angle (degrees) at a vertex to count as a
        triangle tip. Triangles produce ~30-55 deg; squares produce ~90 deg.
        Blobs below this threshold that are NOT highly elongated are treated
        as touching standing+pointer pairs.
    pair_min_aspect : float
        Minimum aspect ratio for a blob to be considered a touching pair.
        Lowered to 1.1 to catch diagonal pointer blobs whose bounding box
        is nearly square even though the shape has a clear triangular tip.
    standing_fill_min : float
        Minimum fill_ratio (pixels / bbox area) to confidently call a blob a
        standing cone (solid square).  A 7x7 square gives ~0.77.  Default 0.65.
    pointer_fill_max : float
        Maximum fill_ratio to confidently call a blob a pointer cone (triangle).
        A 9x7 triangle gives ~0.39.  Default 0.55.  Blobs between pointer_fill_max
        and standing_fill_min are resolved by hull vertex count (3 = triangle).
    max_component_px : int
        Maximum orange-pixel count for a raw connected component.  Blobs
        larger than this are assumed to be legend symbols or artifacts and
        are discarded.  The legend's solid orange square is ~253 px; real
        cone markers are <=120 px.
    pairing_add_standing_threshold_m : float
        If a pointer cone's nearest standing cone is farther than this,
        add a synthetic standing cone at the pointer's location.
    window_px : int
        Half-size of the local window used when analysing each blob's shape.
    min_component_px : int
        Minimum orange-pixel count for a raw connected component to be kept.
    use_quantize : bool
        If True, quantize the image to `quantize_colors` palette entries and
        use palette-based orange detection instead of per-pixel thresholding.
        More robust when JPEG compression blurs the orange channel slightly.
        Falls back to per-pixel thresholding if no orange palette entry found.
    quantize_colors : int
        Number of palette colors to use when use_quantize=True (default 32).
    classifier_labels : str | Path | None
        Path to a labels JSON produced by label_cones.py.  When provided, an
        SVM is trained on the labeled patches and used to classify every blob
        (standing vs pointer) instead of the hand-crafted rules.
        Requires scikit-learn: ``pip install scikit-learn``.
    debug : bool
        If True, save annotated debug images.
    debug_dir : str | None
        Directory for debug images.  Defaults to same dir as image.

    Returns
    -------
    dict with keys:
        'standing'  : list of {'x': float, 'y': float}
        'pointers'  : list of {'x': float, 'y': float, 'heading_deg': float}
        'scale'     : float  (m/px actually used)
        'center'    : (float, float)  image pixel centre (cx, cy)
    """
    img = Image.open(image_path).convert('RGB')
    arr = np.array(img)
    H, W = arr.shape[:2]
    print(f"Image: {W}x{H} px  ({Path(image_path).name})")

    # ------------------------------------------------------------------
    # 1. Scale
    # ------------------------------------------------------------------
    if scale_m_per_px is None:
        scale_m_per_px = detect_grid_scale(arr, grid_feet=grid_feet)
    if scale_m_per_px is None:
        raise ValueError(
            "Could not auto-detect scale from grid.  "
            "Pass --scale <metres_per_pixel> explicitly."
        )
    SCALE = scale_m_per_px

    # ------------------------------------------------------------------
    # 2. Orange pixel detection -> connected components
    # ------------------------------------------------------------------
    if use_quantize:
        ddir = debug_dir or str(Path(image_path).parent)
        omask = quantize_orange_mask(
            img, n_colors=quantize_colors,
            params=mask_params,
            debug=debug, debug_dir=ddir,
        )
        if omask is None:
            omask = orange_mask(arr, params=mask_params)
    else:
        omask = orange_mask(arr, params=mask_params)
    labeled, n_raw = ndimage.label(omask)
    sizes = ndimage.sum(omask, labeled, range(1, n_raw + 1))
    centroids = ndimage.center_of_mass(omask, labeled, range(1, n_raw + 1))

    # Filter tiny and oversized components (oversized = legend/artifact)
    valid = [
        (float(c[1]), float(c[0]), float(s))   # (x, y, weight) in image coords
        for c, s in zip(centroids, sizes)
        if min_component_px <= s <= max_component_px
    ]
    print(f"Raw components {min_component_px}-{max_component_px} px: {len(valid)}")

    # ------------------------------------------------------------------
    # Optional: train SVM classifier from labeled examples
    # ------------------------------------------------------------------
    _classifier = None
    if classifier_labels is not None:
        _classifier = _train_classifier(omask, classifier_labels)

    if not valid:
        return {'standing': [], 'pointers': [], 'scale': SCALE, 'center': (W/2, H/2)}

    pts = [(v[0], v[1]) for v in valid]
    wts = [v[2] for v in valid]

    # ------------------------------------------------------------------
    # 3. Stage-1 merge: very small radius to avoid cross-type merging
    # ------------------------------------------------------------------
    stage1 = greedy_merge(pts, wts, radius=standing_merge_px)
    print(f"After stage-1 merge ({standing_merge_px} px): {len(stage1)} blobs")

    # ------------------------------------------------------------------
    # 4. Classify each blob: standing vs pointer
    # ------------------------------------------------------------------
    standing_blobs = []   # (cx, cy, weight)
    pointer_blobs  = []   # (cx, cy, weight)

    pairs_found = 0
    for cx, cy, w in stage1:
        x0 = max(0, int(cx) - window_px)
        x1 = min(W, int(cx) + window_px)
        y0 = max(0, int(cy) - window_px)
        y1 = min(H, int(cy) + window_px)

        # ------------------------------------------------------------------
        # SVM path: classify using trained patch-based classifier
        # ------------------------------------------------------------------
        if _classifier is not None:
            feat = _extract_patch(omask, cx, cy)
            pred = int(_classifier.predict([feat])[0])
            if pred == 0:
                standing_blobs.append((cx, cy, w))
            else:
                pointer_blobs.append((cx, cy, w))
            continue

        # ------------------------------------------------------------------
        # Rule-based path (default when no classifier provided)
        # ------------------------------------------------------------------
        metrics = classify_blob(omask[y0:y1, x0:x1], cx, cy)
        if metrics is None:
            continue

        fill = metrics['fill_ratio']
        is_elongated = (
            metrics['aspect'] >= pointer_aspect_thresh and
            metrics['max_dim'] >= pointer_min_dim_px
        )
        has_tip = metrics['min_hull_angle'] < tip_angle_thresh
        # Touching pair: convex hull has a clear triangle tip AND blob is at least
        # pointer-sized (not a tiny noise fragment).
        is_pair = (
            has_tip and
            not is_elongated and
            metrics['aspect'] >= pair_min_aspect and
            metrics['max_dim'] >= pointer_min_dim_px
        )
        # Sparse pointer: window-based fill is low (spread-out orange pixels =
        # likely a pointer/pair region) in the classic small-blob size range.
        is_sparse_pointer = (
            not is_elongated and
            not has_tip and
            fill < pointer_fill_max and
            metrics['aspect'] >= pair_min_aspect and
            pointer_min_dim_px <= metrics['max_dim'] <= 17
        )
        # Isolated triangle: the connected component NEAREST to the window centre
        # simplifies to ~3 hull vertices (triangle shape) and fits within the
        # expected pointer size range.  This catches clean triangular markers that
        # are not elongated, have no sharp hull tip on the window level, and are
        # too small to trigger the 15-17 px sparse rule.
        is_isolated_triangle = (
            not is_elongated and
            not has_tip and
            not is_sparse_pointer and
            metrics['hull_vertex_count'] == 3 and
            metrics['iso_max_dim'] >= 5
        )

        if is_elongated:
            pointer_blobs.append((cx, cy, w))
        elif is_pair:
            # Add to BOTH: standing cone at blob centroid, pointer at same centroid
            standing_blobs.append((cx, cy, w))
            pointer_blobs.append((cx, cy, w))
            pairs_found += 1
        elif is_sparse_pointer or is_isolated_triangle:
            pointer_blobs.append((cx, cy, w))
        else:
            standing_blobs.append((cx, cy, w))

    if _classifier is not None:
        print(f"After SVM classification: {len(standing_blobs)} standing, "
              f"{len(pointer_blobs)} pointer blobs")
    else:
        print(f"After classification: {len(standing_blobs)} standing "
              f"({pairs_found} from pairs), {len(pointer_blobs)} pointer blobs")

    # ------------------------------------------------------------------
    # 5. Stage-2 merge within each type
    # ------------------------------------------------------------------
    if standing_blobs:
        spts = [(b[0], b[1]) for b in standing_blobs]
        swts = [b[2] for b in standing_blobs]
        standing_merged = greedy_merge(spts, swts, radius=standing_merge_px)
    else:
        standing_merged = []

    if pointer_blobs:
        ppts = [(b[0], b[1]) for b in pointer_blobs]
        pwts = [b[2] for b in pointer_blobs]
        pointer_merged = greedy_merge(ppts, pwts, radius=pointer_merge_px)
    else:
        pointer_merged = []

    print(f"After stage-2 merge: {len(standing_merged)} standing, "
          f"{len(pointer_merged)} pointer cones")

    # ------------------------------------------------------------------
    # 6. Determine course centre from cone bounds (or fixed override)
    # ------------------------------------------------------------------
    all_cx = [b[0] for b in standing_merged + pointer_merged]
    all_cy = [b[1] for b in standing_merged + pointer_merged]
    if center_pixel is not None:
        X_CENTER = float(center_pixel[0])
        Y_CENTER = float(center_pixel[1])
        print(f"Course centre: pixel ({X_CENTER:.1f}, {Y_CENTER:.1f}) [fixed]")
    else:
        X_CENTER = (min(all_cx) + max(all_cx)) / 2.0
        Y_CENTER = (min(all_cy) + max(all_cy)) / 2.0
        print(f"Course centre: pixel ({X_CENTER:.1f}, {Y_CENTER:.1f})")
    print(f"Blender extents: X={round(min(all_cx)-X_CENTER,1)*SCALE:.1f}..{round(max(all_cx)-X_CENTER,1)*SCALE:.1f}m, "
          f"Y={round(-(max(all_cy)-Y_CENTER),1)*SCALE:.1f}..{round(-(min(all_cy)-Y_CENTER),1)*SCALE:.1f}m")

    def to_blender(px, py):
        return (px - X_CENTER) * SCALE, -(py - Y_CENTER) * SCALE

    # ------------------------------------------------------------------
    # 7. Pointer tip direction (convex hull method)
    # ------------------------------------------------------------------
    pointer_results = []
    for cx, cy, _ in pointer_merged:
        x0 = max(0, int(cx) - window_px)
        x1 = min(W, int(cx) + window_px)
        y0 = max(0, int(cy) - window_px)
        y1 = min(H, int(cy) + window_px)
        local_mask = omask[y0:y1, x0:x1]

        img_heading = tip_direction_from_hull(local_mask, x0, y0)
        # Convert image angle -> Blender angle (Y axis inverted)
        blender_heading = -img_heading
        # Normalise to -180..180
        while blender_heading > 180:
            blender_heading -= 360
        while blender_heading < -180:
            blender_heading += 360

        bx, by = to_blender(cx, cy)
        pointer_results.append({
            'x': round(bx, 3),
            'y': round(by, 3),
            'heading_deg': round(blender_heading, 1),
        })

    # ------------------------------------------------------------------
    # 8. Standing cone results
    # ------------------------------------------------------------------
    standing_results = []
    for cx, cy, _ in standing_merged:
        bx, by = to_blender(cx, cy)
        standing_results.append({'x': round(bx, 3), 'y': round(by, 3)})

    # ------------------------------------------------------------------
    # 9. Pairing check — add standing cone where none is nearby
    # ------------------------------------------------------------------
    added = 0
    for p in pointer_results:
        if not standing_results:
            standing_results.append({'x': p['x'], 'y': p['y']})
            added += 1
            continue
        nearest = min(
            math.hypot(s['x'] - p['x'], s['y'] - p['y'])
            for s in standing_results
        )
        if nearest > pairing_add_standing_threshold_m:
            standing_results.append({'x': p['x'], 'y': p['y']})
            added += 1
    if added:
        print(f"Added {added} standing cones to pair with orphaned pointers")

    # ------------------------------------------------------------------
    # 10. Debug output
    # ------------------------------------------------------------------
    if debug:
        _save_debug_image(
            img, standing_results, pointer_results,
            X_CENTER, Y_CENTER, SCALE,
            debug_dir or str(Path(image_path).parent)
        )

    result = {
        'standing': standing_results,
        'pointers': pointer_results,
        'scale': SCALE,
        'center': (round(X_CENTER, 1), round(Y_CENTER, 1)),
    }
    print(f"\nFinal: {len(standing_results)} standing cones, "
          f"{len(pointer_results)} pointer cones")
    print("(includes any synthetic standing cones added for pointer pairing)")
    return result


# ---------------------------------------------------------------------------
# Debug visualisation
# ---------------------------------------------------------------------------

def _save_debug_image(img, standing, pointers, cx, cy, scale, out_dir):
    scale_factor = min(1.0, 1200 / max(img.width, img.height))
    thumb = img.resize(
        (int(img.width * scale_factor), int(img.height * scale_factor)),
        Image.LANCZOS
    )
    draw = ImageDraw.Draw(thumb)
    sf = scale_factor

    def bld_to_img(bx, by):
        px = bx / scale + cx
        py = -by / scale + cy
        return int(px * sf), int(py * sf)

    for s in standing:
        x, y = bld_to_img(s['x'], s['y'])
        r = 6
        draw.ellipse([x-r, y-r, x+r, y+r], outline='blue', width=2)

    for p in pointers:
        x, y = bld_to_img(p['x'], p['y'])
        h = math.radians(p['heading_deg'])
        # Arrow: base circle + tip line
        r = 5
        draw.ellipse([x-r, y-r, x+r, y+r], outline='red', width=2)
        tx = int(x + 20 * math.cos(h))
        ty = int(y - 20 * math.sin(h))
        draw.line([x, y, tx, ty], fill='red', width=2)

    out_path = Path(out_dir) / 'debug_cones.jpg'
    thumb.save(str(out_path))
    print(f"Debug image saved: {out_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Detect standing and pointer cones from an autocross map image.'
    )
    parser.add_argument('image', help='Path to map image')
    parser.add_argument('--output', '-o', default='cones.json',
                        help='Output JSON path (default: cones.json)')
    parser.add_argument('--scale', type=float, default=None,
                        help='Metres per pixel. Auto-detected from grid if omitted.')
    parser.add_argument('--grid-feet', type=float, default=20.0,
                        help='Grid square size in feet (default: 20)')
    parser.add_argument('--standing-merge', type=int, default=12,
                        help='Stage-1 merge radius in px (default: 12)')
    parser.add_argument('--pointer-merge', type=int, default=55,
                        help='Stage-2 merge radius for pointers in px (default: 55)')
    parser.add_argument('--aspect-thresh', type=float, default=2.5,
                        help='Min aspect ratio for a lone pointer blob (default: 2.5)')
    parser.add_argument('--tip-angle', type=float, default=65.0,
                        help='Max hull vertex angle (deg) to detect a triangle tip (default: 65)')
    parser.add_argument('--standing-fill', type=float, default=0.65,
                        help='Min fill_ratio to classify blob as standing cone (default: 0.65)')
    parser.add_argument('--pointer-fill', type=float, default=0.55,
                        help='Max fill_ratio to classify blob as pointer cone (default: 0.55)')
    parser.add_argument('--center-px', type=float, nargs=2, metavar=('X', 'Y'), default=None,
                        help='Fix image centre pixel (e.g. --center-px 1572 660) instead of auto-detecting from cone bounds')
    parser.add_argument('--red-cones', action='store_true',
                        help='Use red cone mask (for retouched images with solid red markers)')
    parser.add_argument('--quantize', action='store_true',
                        help='Use palette quantization for orange detection (more robust on JPEG)')
    parser.add_argument('--quantize-colors', type=int, default=32,
                        help='Number of palette colors for --quantize (default: 32)')
    parser.add_argument('--classifier', default=None, metavar='LABELS_JSON',
                        help='Path to labels.json from label_cones.py; trains SVM '
                             'to replace rule-based classification (requires scikit-learn)')
    parser.add_argument('--debug', action='store_true',
                        help='Save annotated debug image')
    args = parser.parse_args()

    mask_params = RED_MASK_PARAMS if args.red_cones else None
    max_comp = 400 if args.red_cones else 200

    result = detect_cones_from_map(
        image_path=args.image,
        scale_m_per_px=args.scale,
        grid_feet=args.grid_feet,
        standing_merge_px=args.standing_merge,
        pointer_merge_px=args.pointer_merge,
        pointer_aspect_thresh=args.aspect_thresh,
        tip_angle_thresh=args.tip_angle,
        standing_fill_min=args.standing_fill,
        pointer_fill_max=args.pointer_fill,
        mask_params=mask_params,
        center_pixel=args.center_px,
        max_component_px=max_comp,
        use_quantize=args.quantize,
        quantize_colors=args.quantize_colors,
        classifier_labels=args.classifier,
        debug=args.debug,
    )

    with open(args.output, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Results saved to: {args.output}")


if __name__ == '__main__':
    main()
