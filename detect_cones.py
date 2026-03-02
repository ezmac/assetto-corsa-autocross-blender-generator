"""
detect_cones.py — Autocross cone detector for Assetto Corsa track building.

Reads a course map image, detects cone positions by color, and outputs
Blender world-space coordinates using a two-point GCP calibration.

Color conventions (magenta-pointer variant):
  Orange  FF8C00-ish  — standing cones (upright)
  Magenta FF00FF      — pointer cones (lying flat)
  Green               — timing start gate cones
  Red                 — timing end gate cones
  Blue                — GCP reference dots

Outputs JSON with separate lists for standing, pointer, green, red, blue.
Pointer entries include a 'facing_deg' field (angle in XY plane toward the
nearest standing cone) for use when setting Z-rotation in Blender.

Usage:
    python detect_cones.py  --image <path.png>
                            --out   <output.json>
                            [--preview <annotated.png>]
                            [--gcp-left-img  X Y]
                            [--gcp-left-blender BX BY]
                            [--gcp-right-img X Y]
                            [--gcp-right-blender BX BY]

Defaults use the Seneca GP 2021 GCP calibration.
"""

import sys
import json
import math
import argparse
import numpy as np
from PIL import Image
import scipy.ndimage as nd

Image.MAX_IMAGE_PIXELS = None  # large maps exceed PIL's default limit

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Seneca GP 2021 GCP calibration (3-point affine)
# Pixel coords updated when blue dots were moved inside the driving area.
# third_img/third_blender = AC_POBJECT_GCP_P_BOTTOM_RIGHT (east bump-out corner, ~3.1m outside)
DEFAULT_GCP = {
    "left_img":       (11371, 4248),
    "left_blender":   (-37.185, 105.608),
    "right_img":      (19734, 4246),
    "right_blender":  (154.726, 109.324),
    "third_img":      (23730, 13437),
    "third_blender":  (246.058, -96.129),
}

WORK_WIDTH = 3000   # downsample to this width for detection

# Merge radius (working-image pixels) for combining sub-pixel fragments of
# the SAME cone symbol. Keep well below the smallest real cone-to-cone gap.
# At WORK_WIDTH=3000, scale ≈ 0.196 m/px:
#   12-ft gate ≈ 18.7 px   ← must NOT merge across this
#   same-cone fragments are typically < 8 px apart ← must merge these
SAME_CONE_MERGE_RADIUS = 10   # px for standing cones — safe below the 18.7 px gate threshold

# Pointer cones can be as close as 3" to each other (~8 px center-to-center
# at 3000 px working width). The merge radius must stay below that distance
# while still bridging within-cone pixel fragments (~3 px scatter).
POINTER_MERGE_RADIUS = 4

# Minimum connected-component size (px) to be considered a blob at all.
MIN_BLOB_PX = 3

# Pointer snap — physically correct spacing from the standing cone edge.
# Gap is the clear space between adjacent cone surfaces (tip-to-base edge).
POINTER_SNAP_GAP_M = 0.0762        # 3 inches tip-to-edge gap
# Cone template dimensions (must match AC_POBJECT_MovableCone mesh in template).
CONE_BASE_RADIUS_M = 0.1397        # 5.5"  (11" base diameter)
CONE_HEIGHT_M      = 0.4318        # 17" tall
# First pointer center from standing center:
#   standing_edge + gap + half_pointer_height = 5.5" + 3" + 8.5" = 17"
# Each subsequent pointer steps by: cone_height + gap = 17" + 3" = 20"
#
# Anchor radius: the CLOSEST pointer to a standing cone must be within this
# distance for the chain to be snapped at all.  Subsequent chain members can
# be anywhere — they follow the anchor's direction.
POINTER_SNAP_ANCHOR_RADIUS_M = 5.0


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------

def build_transform(gcp):
    """Build a coordinate transform from GCP calibration points.

    Two-point mode (default):
        Uses X-axis separation only.  Assumes horizontal alignment of GCPs.
        Returns {'type': 'scale', 'scale': ..., 'ox': ..., 'oy': ...}

    Three-point affine mode (when gcp has 'third_img' / 'third_blender'):
        Solves a full 2-D affine transform (6 parameters) that corrects for
        scale, rotation, and shear between image and Blender coordinate systems.
        Returns {'type': 'affine', 'ax': [a11,a12,tx], 'ay': [a21,a22,ty]}

        Transform:  bx = ax[0]*px + ax[1]*py + ax[2]
                    by = ay[0]*px + ay[1]*py + ay[2]
    """
    has_third = "third_img" in gcp and "third_blender" in gcp

    if has_third:
        pts_img = np.array([gcp["left_img"],     gcp["right_img"],     gcp["third_img"]],
                           dtype=float)
        pts_bl  = np.array([gcp["left_blender"], gcp["right_blender"], gcp["third_blender"]],
                           dtype=float)
        P  = np.column_stack([pts_img, np.ones(3)])   # 3×3 design matrix
        ax = np.linalg.solve(P, pts_bl[:, 0])          # a11, a12, tx
        ay = np.linalg.solve(P, pts_bl[:, 1])          # a21, a22, ty
        return {"type": "affine", "ax": ax.tolist(), "ay": ay.tolist()}
    else:
        dx_img = gcp["right_img"][0]     - gcp["left_img"][0]
        dx_bl  = gcp["right_blender"][0] - gcp["left_blender"][0]
        scale  = dx_bl / dx_img
        ox = gcp["left_blender"][0] - scale * gcp["left_img"][0]
        oy = gcp["left_blender"][1] + scale * gcp["left_img"][1]
        return {"type": "scale", "scale": scale, "ox": ox, "oy": oy}


def to_blender(px_orig, py_orig, transform):
    if transform["type"] == "affine":
        ax, ay = transform["ax"], transform["ay"]
        bx = ax[0] * px_orig + ax[1] * py_orig + ax[2]
        by = ay[0] * px_orig + ay[1] * py_orig + ay[2]
        return bx, by
    else:
        s, ox, oy = transform["scale"], transform["ox"], transform["oy"]
        return s * px_orig + ox, -s * py_orig + oy


# ---------------------------------------------------------------------------
# Blob detection / merging
# ---------------------------------------------------------------------------

def detect_blobs(mask, min_size=MIN_BLOB_PX):
    """Label connected components in mask; return list of blob dicts."""
    labeled, n = nd.label(mask)
    blobs = []
    for i in range(1, n + 1):
        pix = np.where(labeled == i)
        sz = len(pix[0])
        if sz < min_size:
            continue
        ys, xs = pix[0], pix[1]
        blobs.append({
            "cx":   float(xs.mean()),
            "cy":   float(ys.mean()),
            "size": sz,
            "bw":   int(xs.max() - xs.min() + 1),
            "bh":   int(ys.max() - ys.min() + 1),
        })
    return blobs


def merge_blobs(blobs, radius):
    """Greedily merge blobs whose centroids are within radius pixels.
    Uses pixel-count-weighted centroid so larger fragments dominate.
    NOTE: greedy single-pass — first blob anchors each group. For very
    tight clusters this can mis-group; a proper union-find would be cleaner.
    """
    used = [False] * len(blobs)
    merged = []
    for i, a in enumerate(blobs):
        if used[i]:
            continue
        group = [a]
        used[i] = True
        for j, b in enumerate(blobs):
            if used[j]:
                continue
            if (a["cx"] - b["cx"]) ** 2 + (a["cy"] - b["cy"]) ** 2 < radius ** 2:
                group.append(b)
                used[j] = True
        total = sum(g["size"] for g in group)
        merged.append({
            "cx":   sum(g["cx"]   * g["size"] for g in group) / total,
            "cy":   sum(g["cy"]   * g["size"] for g in group) / total,
            "size": total,
            "bw":   max(g["bw"] for g in group),
            "bh":   max(g["bh"] for g in group),
        })
    return merged


def split_merged_blobs(mask, blobs, split_threshold=1.6):
    """Detect blobs that contain multiple fused cone symbols and split them.

    Strategy (same principle as what makes orange work):
      - A single cone symbol has a consistent pixel footprint.
      - A double-pointer appears as one blob with ~2x that footprint.
      - We estimate N = round(blob.size / reference_size) and, when N > 1,
        recover N sub-centroids using PCA along the blob's principal axis.

    The reference size is the median of the smallest half of blobs — this
    anchors on clear single-cone detections and ignores already-merged ones.

    Args:
        mask:  the boolean 2D mask used to produce blobs (needed to get pixels).
        blobs: list of blob dicts from merge_blobs.
        split_threshold: blobs with size > threshold * ref_size are split.

    Returns:
        New list of blobs.  Oversized blobs are replaced by N sub-blobs;
        each sub-blob gets the same size as reference_size so downstream
        size-checks remain consistent.
    """
    if not blobs:
        return blobs

    sizes = sorted(b["size"] for b in blobs)
    # Reference = median of the lower half (single-cone representatives)
    lower_half = sizes[: max(1, len(sizes) // 2)]
    ref_size = float(np.median(lower_half))
    if ref_size < 1:
        return blobs

    labeled, _ = nd.label(mask)

    result = []
    for b in blobs:
        n = round(b["size"] / ref_size)
        if n <= 1 or b["size"] < split_threshold * ref_size:
            result.append(b)
            continue

        # Retrieve the actual pixels of this blob by finding the label
        # whose centroid is closest to b["cx"], b["cy"].
        # (We can't directly map back, so we find the nearest label centroid.)
        pix_all = np.where(labeled > 0)
        if len(pix_all[0]) == 0:
            result.append(b)
            continue

        # Find the connected-component label at the blob centroid pixel
        cy_int = int(round(b["cy"]))
        cx_int = int(round(b["cx"]))
        # Clamp to image bounds
        h, w = mask.shape
        cy_int = min(max(cy_int, 0), h - 1)
        cx_int = min(max(cx_int, 0), w - 1)
        lbl = int(labeled[cy_int, cx_int])

        if lbl == 0:
            # Centroid landed outside the mask; find nearest labeled pixel
            dists = (pix_all[0] - cy_int) ** 2 + (pix_all[1] - cx_int) ** 2
            nearest = np.argmin(dists)
            lbl = int(labeled[pix_all[0][nearest], pix_all[1][nearest]])

        pix = np.where(labeled == lbl)
        if len(pix[0]) < 2:
            result.append(b)
            continue

        pts = np.column_stack([pix[1].astype(float),  # x
                               pix[0].astype(float)])  # y

        # PCA: find principal axis of the pixel cloud
        centered = pts - pts.mean(axis=0)
        cov = np.cov(centered.T)
        if cov.ndim < 2:
            # Degenerate blob (single row/col of pixels)
            result.append(b)
            continue
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        # Eigenvector with largest eigenvalue = principal axis
        principal = eigenvectors[:, np.argmax(eigenvalues)]

        # Project all pixels onto the principal axis
        projections = centered @ principal

        # Place N sub-centroids equally along the principal axis
        proj_min, proj_max = projections.min(), projections.max()
        if proj_max == proj_min:
            result.append(b)
            continue

        mean_xy = pts.mean(axis=0)
        spacing = (proj_max - proj_min) / (n - 1) if n > 1 else 0

        for k in range(n):
            t = proj_min + k * spacing
            sub_cx = mean_xy[0] + t * principal[0]
            sub_cy = mean_xy[1] + t * principal[1]
            result.append({
                "cx":   float(sub_cx),
                "cy":   float(sub_cy),
                "size": int(ref_size),   # normalize so all sub-cones look equal
                "bw":   b["bw"] // n,
                "bh":   b["bh"],
                "split_from": n,         # audit trail
            })

    return result


# ---------------------------------------------------------------------------
# Color masks
# ---------------------------------------------------------------------------

def detect_orange(arr):
    """Standing cones — orange (≈ FF8C00)."""
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return (
        (R > 180) &
        (G > 80) & (G < 200) &
        (B < 100) &
        (R.astype(int) > G.astype(int) + 50)
    )


def detect_magenta(arr):
    """Pointer cones — magenta (FF00FF)."""
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return (
        (R > 180) &
        (G < 80) &
        (B > 180)
    )


def detect_green(arr, exclude=None):
    """Timing start cones — green."""
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mask = (
        (G > 100) &
        (R < 180) &
        (B < 180) &
        (G.astype(int) > R.astype(int) + 30) &
        (G.astype(int) > B.astype(int) + 20)
    )
    if exclude is not None:
        mask = mask & ~exclude
    return mask


def detect_red(arr, exclude=None):
    """Timing end cones — red."""
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mask = (R > 150) & (G < 100) & (B < 100)
    if exclude is not None:
        mask = mask & ~exclude
    return mask


def detect_blue(arr):
    """GCP reference dots — blue."""
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return (
        (B > 150) &
        (R < 120) &
        (G < 150) &
        (B.astype(int) > R.astype(int) + 50)
    )


# ---------------------------------------------------------------------------
# Pointer orientation
# ---------------------------------------------------------------------------

def assign_pointer_facing(pointers, standing):
    """For each pointer cone, find the nearest standing cone and compute
    the angle (degrees, CCW from +X axis) from pointer toward standing.
    Stored as 'facing_deg' in each pointer entry.

    This angle is the Z-rotation to apply in Blender so the pointer cone
    faces its associated standing cone.
    """
    if not standing:
        for p in pointers:
            p["facing_deg"] = None
        return

    stand_arr = np.array([[s["bx"], s["by"]] for s in standing])

    for p in pointers:
        dx = stand_arr[:, 0] - p["bx"]
        dy = stand_arr[:, 1] - p["by"]
        dists = dx * dx + dy * dy
        nearest = int(np.argmin(dists))
        angle = math.degrees(math.atan2(dy[nearest], dx[nearest]))
        p["facing_deg"] = round(angle, 1)
        p["nearest_standing_idx"] = nearest


# ---------------------------------------------------------------------------
# Pointer snapping
# ---------------------------------------------------------------------------

def snap_pointers_to_standing(pointers, standing,
                               gap_m=POINTER_SNAP_GAP_M,
                               anchor_radius_m=POINTER_SNAP_ANCHOR_RADIUS_M,
                               timing_cones=None):
    """Reposition pointer chains to physically correct positions from their standing cone.

    For each standing cone:
      1. Collect ALL pointers assigned to it (via nearest_standing_idx).
      2. The CLOSEST pointer is the chain anchor — if it exceeds anchor_radius_m,
         the chain is skipped (likely a stray detection with no real nearby pointer).
      3. The anchor's direction toward the standing cone defines the chain axis.
         Remaining chain members follow this axis regardless of how far they are
         from the standing cone in the original image.
      4. Pointer centers are placed at:
           chain 0: base_radius + gap + cone_height/2  (tip is gap past standing edge)
           chain 1: chain_0 + cone_height + gap        (tip is gap past prev cone base)
           chain n: chain_0 + n * (cone_height + gap)

    Updates 'bx', 'by', 'facing_deg' in-place.  Adds 'snapped': True for audit.
    Returns the number of pointers repositioned.
    """
    if not standing or not pointers:
        return 0

    from collections import defaultdict
    groups = defaultdict(list)

    for p in pointers:
        s_idx = p.get("nearest_standing_idx")
        if s_idx is None:
            continue
        s = standing[s_idx]
        dx = s["bx"] - p["bx"]
        dy = s["by"] - p["by"]
        dist = math.sqrt(dx * dx + dy * dy)
        groups[s_idx].append((p, dist))

    # Distances along the chain from the standing cone center.
    # first_dist: center of pointer 0 (tip is exactly gap_m past standing base edge)
    # step_dist:  center-to-center spacing for subsequent pointers
    first_dist = CONE_BASE_RADIUS_M + gap_m + CONE_HEIGHT_M / 2   # = 17" for standard cones
    step_dist  = CONE_HEIGHT_M + gap_m                             # = 20" for standard cones

    moved = 0
    for s_idx, group in groups.items():
        s = standing[s_idx]
        sx, sy = s["bx"], s["by"]

        # Sort by distance to standing; the anchor (index 0) must be within radius
        group.sort(key=lambda t: t[1])
        anchor_p, anchor_dist = group[0]
        if anchor_dist > anchor_radius_m:
            continue  # no pointer close enough to anchor this chain

        # Chain axis: direction from the ANCHOR pointer toward the standing cone.
        # Using the anchor (not the mean) keeps the axis stable even when distant
        # chain members were detected at inaccurate image positions.
        adx = sx - anchor_p["bx"]
        ady = sy - anchor_p["by"]
        d = math.sqrt(adx * adx + ady * ady)
        if d < 1e-6:
            continue
        ux, uy = adx / d, ady / d   # unit vector: pointer → standing
        facing = round(math.degrees(math.atan2(uy, ux)), 1)

        for chain_n, (p, _) in enumerate(group):
            d_from_standing = first_dist + chain_n * step_dist
            # Subtract ux/uy (toward standing) to move AWAY from standing
            p["bx"] = round(sx - ux * d_from_standing, 3)
            p["by"] = round(sy - uy * d_from_standing, 3)
            p["facing_deg"] = facing
            p["snapped"] = True
            moved += 1

    # Second pass: snap any still-unsnapped pointers to nearby timing cones.
    # This handles pointers adjacent to green/red gate cones, which are not in
    # the standing (orange) cone list and therefore missed by the first pass.
    if timing_cones:
        for p in pointers:
            if p.get("snapped"):
                continue
            best_dist = float("inf")
            best_tc   = None
            for tc in timing_cones:
                dx = tc["bx"] - p["bx"]
                dy = tc["by"] - p["by"]
                d  = math.sqrt(dx * dx + dy * dy)
                if d < best_dist:
                    best_dist = d
                    best_tc   = tc
            if best_tc is None or best_dist > anchor_radius_m:
                continue
            adx = best_tc["bx"] - p["bx"]
            ady = best_tc["by"] - p["by"]
            d   = math.sqrt(adx * adx + ady * ady)
            if d < 1e-6:
                continue
            ux, uy  = adx / d, ady / d
            facing  = round(math.degrees(math.atan2(uy, ux)), 1)
            d_place = CONE_BASE_RADIUS_M + gap_m + CONE_HEIGHT_M / 2
            p["bx"] = round(best_tc["bx"] - ux * d_place, 3)
            p["by"] = round(best_tc["by"] - uy * d_place, 3)
            p["facing_deg"] = facing
            p["snapped"]    = True
            p["snapped_to_timing"] = True
            moved += 1

    return moved


# ---------------------------------------------------------------------------
# JSON helper
# ---------------------------------------------------------------------------

def native(obj):
    """Recursively convert numpy scalars to Python builtins for JSON."""
    if isinstance(obj, dict):
        return {k: native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [native(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(image_path, gcp, out_path, preview_path=None, snap_pointers=True, snap_radius_m=POINTER_SNAP_ANCHOR_RADIUS_M):
    # --- Load & downsample ---
    print(f"Loading {image_path} ...", file=sys.stderr)
    img = Image.open(image_path).convert("RGBA")
    W, H = img.size
    sf = W / WORK_WIDTH
    img_small = img.resize((WORK_WIDTH, int(H / sf)), Image.LANCZOS)
    print(f"  Original: {W}×{H}  Working: {img_small.size}  sf={sf:.2f}", file=sys.stderr)

    transform = build_transform(gcp)
    if transform["type"] == "affine":
        print(f"  Transform: 3-point affine", file=sys.stderr)
    else:
        print(f"  Scale: {transform['scale']:.6f} m/px (original pixels)", file=sys.stderr)

    arr = np.array(img_small)

    # --- Detect each color independently ---
    orange_mask  = detect_orange(arr)
    magenta_mask = detect_magenta(arr)
    # Exclude orange from green/red to avoid overlap; magenta won't bleed into
    # red (its B channel is high) but exclude it explicitly to be safe.
    combined_exclude = orange_mask | magenta_mask
    green_mask  = detect_green(arr, exclude=combined_exclude)
    red_mask    = detect_red(arr,   exclude=combined_exclude)
    blue_mask   = detect_blue(arr)

    def process(mask, label, merge_r=SAME_CONE_MERGE_RADIUS, min_sz=MIN_BLOB_PX):
        raw   = detect_blobs(mask, min_size=min_sz)
        mrgd  = merge_blobs(raw, merge_r)
        print(f"  {label}: {len(raw)} raw → {len(mrgd)} merged", file=sys.stderr)
        return mrgd

    orange_merged  = process(orange_mask,  "Orange (standing)")

    # Magenta: merge first, then split oversized blobs that contain fused
    # double/triple pointer sets.  This is the same principle as orange —
    # ensure each surviving entry maps to exactly one cone symbol.
    magenta_raw    = detect_blobs(magenta_mask, min_size=MIN_BLOB_PX)
    magenta_merged = merge_blobs(magenta_raw, POINTER_MERGE_RADIUS)
    magenta_merged = split_merged_blobs(magenta_mask, magenta_merged)
    n_split = sum(1 for b in magenta_merged if b.get("split_from", 1) > 1)
    print(f"  Magenta (pointer): {len(magenta_raw)} raw → {len(magenta_merged)} "
          f"after split ({n_split} blobs were subdivided)", file=sys.stderr)
    green_merged   = process(green_mask,   "Green  (t-start) ", merge_r=5)
    red_merged     = process(red_mask,     "Red    (t-end)   ", merge_r=5)
    blue_merged    = process(blue_mask,    "Blue   (GCP)     ", merge_r=10)

    # --- Choose pointer source ---
    # Prefer magenta (magenta-pointer image variant).  Fall back to orange for
    # images where pointer cones are drawn orange instead of magenta.  The
    # caller asserts that only one color will yield detections for a given image.
    if len(magenta_merged) > 0:
        pointer_blobs  = magenta_merged
        pointer_source = "magenta"
    else:
        pointer_blobs  = orange_merged
        pointer_source = "orange"
    print(f"  Pointer source: {pointer_source} ({len(pointer_blobs)} cones)", file=sys.stderr)

    # --- Convert to Blender coords ---
    def to_bl_list(blobs, cone_type):
        out = []
        for b in blobs:
            bx, by = to_blender(b["cx"] * sf, b["cy"] * sf, transform)
            out.append({
                "bx":   round(bx, 2),
                "by":   round(by, 2),
                "type": cone_type,
                "size": b["size"],
            })
        return out

    standing = to_bl_list(orange_merged, "standing")
    pointers = to_bl_list(pointer_blobs, "pointer")
    greens   = to_bl_list(green_merged,   "timing_start")
    reds     = to_bl_list(red_merged,     "timing_end")
    blues    = to_bl_list(blue_merged,    "gcp")

    # --- Pointer orientation ---
    assign_pointer_facing(pointers, standing)

    # --- Snap pointers to exact 3" increments from their standing cone ---
    if snap_pointers:
        timing = greens + reds
        n_snapped = snap_pointers_to_standing(
            pointers, standing,
            anchor_radius_m=snap_radius_m,
            timing_cones=timing if timing else None,
        )
        print(f"  Snapped {n_snapped}/{len(pointers)} pointers to 3\" increments "
              f"(radius={snap_radius_m}m)", file=sys.stderr)
    else:
        print(f"  Pointer snap disabled (--no-snap-pointers)", file=sys.stderr)

    # --- Bounding box ---
    all_cones = standing + pointers
    bounds = {}
    if all_cones:
        bxs = [c["bx"] for c in all_cones]
        bys = [c["by"] for c in all_cones]
        bounds = {
            "xmin": round(min(bxs), 1), "xmax": round(max(bxs), 1),
            "ymin": round(min(bys), 1), "ymax": round(max(bys), 1),
        }

    result = {
        "transform":      transform,
        "pointer_source": pointer_source,
        "n_standing": len(standing),
        "n_pointer":  len(pointers),
        "n_green":    len(greens),
        "n_red":      len(reds),
        "n_blue":     len(blues),
        "bounds":     bounds,
        "standing":   standing,
        "pointers":   pointers,
        "greens":     greens,
        "reds":       reds,
        "blues":      blues,
    }

    with open(out_path, "w") as f:
        json.dump(native(result), f, indent=2)
    print(f"  Wrote: {out_path}", file=sys.stderr)

    # --- Annotated preview ---
    if preview_path:
        from PIL import ImageDraw
        vis = img_small.convert("RGB")
        draw = ImageDraw.Draw(vis)

        r_stand = 5
        r_ptr   = 4

        for b, c in zip(orange_merged, standing):
            # Standing cone: dark red circle
            draw.ellipse(
                [b["cx"] - r_stand, b["cy"] - r_stand,
                 b["cx"] + r_stand, b["cy"] + r_stand],
                outline=(180, 30, 30), width=2)

        for b, c in zip(pointer_blobs, pointers):
            was_split = b.get("split_from", 1) > 1
            # Split blobs: cyan outline so they stand out for review
            outline_color = (0, 200, 200) if was_split else (220, 0, 220)
            draw.ellipse(
                [b["cx"] - r_ptr, b["cy"] - r_ptr,
                 b["cx"] + r_ptr, b["cy"] + r_ptr],
                outline=outline_color, width=2)
            if c.get("facing_deg") is not None:
                rad = math.radians(c["facing_deg"])
                ex = b["cx"] + 10 * math.cos(rad)
                ey = b["cy"] - 10 * math.sin(rad)  # image Y is flipped
                draw.line([b["cx"], b["cy"], ex, ey], fill=outline_color, width=1)

        for b in green_merged:
            draw.rectangle(
                [b["cx"] - 6, b["cy"] - 6, b["cx"] + 6, b["cy"] + 6],
                outline=(0, 200, 0), width=2)

        for b in red_merged:
            draw.rectangle(
                [b["cx"] - 6, b["cy"] - 6, b["cx"] + 6, b["cy"] + 6],
                outline=(200, 0, 0), width=2)

        for b in blue_merged:
            draw.ellipse(
                [b["cx"] - 5, b["cy"] - 5, b["cx"] + 5, b["cy"] + 5],
                outline=(0, 0, 220), width=3)

        vis.save(preview_path)
        print(f"  Preview: {preview_path}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image",   required=True,  help="Input PNG course map")
    p.add_argument("--out",     required=True,  help="Output JSON path")
    p.add_argument("--preview", default=None,   help="Optional annotated PNG output")
    p.add_argument("--gcp-left-img",      nargs=2, type=float, default=None,
                   metavar=("X", "Y"), help="GCP left pixel coords in original image")
    p.add_argument("--gcp-left-blender",  nargs=2, type=float, default=None,
                   metavar=("BX", "BY"), help="GCP left Blender world coords")
    p.add_argument("--gcp-right-img",     nargs=2, type=float, default=None,
                   metavar=("X", "Y"))
    p.add_argument("--gcp-right-blender", nargs=2, type=float, default=None,
                   metavar=("BX", "BY"))
    p.add_argument("--gcp3-img",      nargs=2, type=float, default=None,
                   metavar=("X", "Y"), help="Third GCP pixel coords (enables affine transform)")
    p.add_argument("--gcp3-blender",  nargs=2, type=float, default=None,
                   metavar=("BX", "BY"), help="Third GCP Blender world coords")
    p.add_argument("--no-snap-pointers", action="store_true", default=False,
                   help="Disable snapping pointer cones to physically correct positions")
    p.add_argument("--snap-radius", type=float, default=POINTER_SNAP_ANCHOR_RADIUS_M,
                   metavar="M",
                   help=f"Max distance (m) from standing cone to its ANCHOR pointer "
                        f"(closest in chain) for snapping to apply. Remaining chain "
                        f"members are snapped regardless of distance. "
                        f"(default: {POINTER_SNAP_ANCHOR_RADIUS_M})")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    gcp = dict(DEFAULT_GCP)
    if args.gcp_left_img:
        gcp["left_img"]      = tuple(args.gcp_left_img)
    if args.gcp_left_blender:
        gcp["left_blender"]  = tuple(args.gcp_left_blender)
    if args.gcp_right_img:
        gcp["right_img"]     = tuple(args.gcp_right_img)
    if args.gcp_right_blender:
        gcp["right_blender"] = tuple(args.gcp_right_blender)
    if args.gcp3_img:
        gcp["third_img"]     = tuple(args.gcp3_img)
    if args.gcp3_blender:
        gcp["third_blender"] = tuple(args.gcp3_blender)

    run(args.image, gcp, args.out, preview_path=args.preview,
        snap_pointers=not args.no_snap_pointers,
        snap_radius_m=args.snap_radius)
