"""
detect_cones_pdf.py — PDF course map cone detector for Assetto Corsa track building.

Reads an autocross course map PDF (SCCA Solo Nationals, Lincoln Airpark 2009-2018),
extracts vector paths to detect cone positions, and outputs Blender world-space
coordinates centred on the course midpoint.

Shape conventions (vector PDF from Adobe Illustrator):
  Standing cones : small filled near-black circles  (~4 pt diameter, 4 bezier curves)
  Pointer cones  : small filled near-black polygons (~7 pt, line-segment paths)
  Large triangles: course-direction arrows — filtered out (area > MAX_CONE_AREA_PT2)

Scale: the maps are drawn at 1 PDF point = 1 ft (confirmed by the source site).
  M_PER_PT = 0.3048  (metres per PDF point, exact)

Usage:
    python detect_cones_pdf.py \\
        --pdf  "SoloNationals/.../2018 Nationals Courses.pdf" \\
        --page 2 \\
        --out  west_2018.json \\
        [--preview west_2018.png] \\
        [--no-snap-pointers]
"""

import sys
import json
import math
import argparse
import numpy as np
import fitz  # pymupdf

from detect_cones import (
    assign_pointer_facing,
    snap_pointers_to_standing,
    native,
    CONE_BASE_RADIUS_M,
    CONE_HEIGHT_M,
    POINTER_SNAP_GAP_M,
    POINTER_SNAP_ANCHOR_RADIUS_M,
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

DARK_THRESHOLD  = 0.35   # fill RGB channels must all be below this (0–1)
MIN_CONE_AREA   = 5.0    # pt²  — filter noise / stray dots
MAX_CONE_AREA   = 200.0  # pt²  — filter large course-boundary shapes
MAX_ASPECT      = 4.0    # max(w/h, h/w) for a cone bounding box

# Course-outline texture generation
COURSE_LINE_PX_PER_M = 4.0    # texture resolution (4 px/m ≈ 0.25 m/px)
COURSE_LINE_MAX_PX   = 4096   # max texture dimension
COURSE_LINE_WIDTH_M  = 0.15   # rendered stroke width in metres

# Classification: circles have bezier curves; polygons have line segments.
# The n=8 arrow polygon (pointer arrow symbol) and n=3 triangle are both pointers.
# Shapes with ≥2 curves and no lines → standing (circle).
MIN_CURVES_FOR_CIRCLE = 2

# Some courses (e.g. 2019 west) render filled dots as many overlapping tiny nl≤2
# triangular slices instead of bezier circles.  Cluster detection parameters:
DOT_CLUSTER_RADIUS_PT = 4.0   # pt — bin radius for grouping micro-shapes
DOT_CLUSTER_MIN_COUNT = 6     # min shapes per cluster to call it a standing cone

# Scale: source maps are drawn at 1 PDF point = 1 ft (confirmed by source).
M_PER_PT = 0.3048   # metres per PDF point (exact: 1 ft = 0.3048 m)


# ---------------------------------------------------------------------------
# Cone candidate extraction
# ---------------------------------------------------------------------------

def is_dark(fill):
    if fill is None:
        return False
    return fill[0] < DARK_THRESHOLD and fill[1] < DARK_THRESHOLD and fill[2] < DARK_THRESHOLD


def detect_dot_clusters(drawings):
    """Find standing cones rendered as many tiny overlapping shapes.

    Some Illustrator PDFs export filled circles as dozens of degenerate nl≤2
    triangular slices at the same position.  These are rejected by the normal
    classifier (area too small, or nl<3 for pointers).  This function bins all
    dark filled micro-shapes (area < MIN_CONE_AREA) by grid cell and returns
    one standing-cone candidate per cell that contains enough shapes.

    Returns list of {"pdf_x": float, "pdf_y": float}.
    """
    cell = DOT_CLUSTER_RADIUS_PT * 2  # grid cell side length
    grid = {}   # (grid_ix, grid_iy) → list of (cx, cy)

    for d in drawings:
        fill = d.get("fill")
        if not is_dark(fill):
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        area = (rect.x1 - rect.x0) * (rect.y1 - rect.y0)
        if area >= MIN_CONE_AREA:
            continue  # handled by normal classifier
        items = d.get("items", [])
        n_line = sum(1 for it in items if it[0] == "l")
        n_curv = sum(1 for it in items if it[0] == "c")
        if n_curv >= MIN_CURVES_FOR_CIRCLE:
            continue  # small circles handled normally
        if n_line > 3:
            continue  # real pointer triangles handled normally
        cx = (rect.x0 + rect.x1) / 2
        cy = (rect.y0 + rect.y1) / 2
        key = (int(cx // cell), int(cy // cell))
        grid.setdefault(key, []).append((cx, cy))

    raw = []
    for pts in grid.values():
        if len(pts) < DOT_CLUSTER_MIN_COUNT:
            continue
        avg_x = sum(p[0] for p in pts) / len(pts)
        avg_y = sum(p[1] for p in pts) / len(pts)
        raw.append({"pdf_x": avg_x, "pdf_y": avg_y})

    # Merge clusters that straddle a grid-cell boundary (within one cell radius).
    merged = []
    used = set()
    for i, a in enumerate(raw):
        if i in used:
            continue
        group_x = [a["pdf_x"]]; group_y = [a["pdf_y"]]
        for j, b in enumerate(raw):
            if j <= i or j in used:
                continue
            if (abs(b["pdf_x"] - a["pdf_x"]) <= cell and
                    abs(b["pdf_y"] - a["pdf_y"]) <= cell):
                group_x.append(b["pdf_x"])
                group_y.append(b["pdf_y"])
                used.add(j)
        used.add(i)
        merged.append({"pdf_x": sum(group_x) / len(group_x),
                        "pdf_y": sum(group_y) / len(group_y)})
    return merged


def get_all_vertices(items):
    """Return list of (x, y) tuples for all endpoints in a path's items list."""
    pts = []
    for it in items:
        if it[0] == "l":
            _, p1, p2 = it
            pts.append((p1.x, p1.y))
            pts.append((p2.x, p2.y))
        elif it[0] == "c":
            _, p1, p2, p3, p4 = it
            pts.append((p1.x, p1.y))
            pts.append((p4.x, p4.y))
    # Deduplicate while preserving order (use dict key trick)
    seen = {}
    for p in pts:
        key = (round(p[0], 2), round(p[1], 2))
        if key not in seen:
            seen[key] = p
    return list(seen.values())


def compute_tip_angle(verts, cx, cy):
    """Angle (degrees CCW from +X, Blender coords) from centroid to triangle tip.

    The 'tip' is the vertex farthest from the centroid (sharpest point).
    Y is negated because pymupdf uses y-down but Blender uses y-up.
    """
    if not verts:
        return None
    best_d2 = -1.0
    tip = verts[0]
    for v in verts:
        d2 = (v[0] - cx) ** 2 + (v[1] - cy) ** 2
        if d2 > best_d2:
            best_d2 = d2
            tip = v
    dx = tip[0] - cx
    dy = tip[1] - cy
    return round(math.degrees(math.atan2(-dy, dx)), 1)   # flip Y


def classify_candidate(d):
    """Classify one candidate drawing.

    Returns ('standing'|'pointer', cx, cy, tip_angle_or_None) or None to skip.
    """
    fill = d.get("fill")
    if not is_dark(fill):
        return None

    rect  = d["rect"]
    w, h  = rect.width, rect.height
    if w < 0.5 or h < 0.5:
        return None

    items  = d.get("items", [])
    n_line = sum(1 for it in items if it[0] == "l")
    n_curv = sum(1 for it in items if it[0] == "c")
    n_rect = sum(1 for it in items if it[0] == "re")

    if n_curv >= MIN_CURVES_FOR_CIRCLE:
        # Circle → standing cone.  Course-outline lines may be attached to the same
        # compound path; filter and centroid on bezier endpoints only so that a long
        # attached line doesn't inflate the bounding box and cause a false rejection.
        bezier_pts = []
        for it in items:
            if it[0] == "c":
                bezier_pts.append((it[1].x, it[1].y))
                bezier_pts.append((it[4].x, it[4].y))
        if not bezier_pts:
            return None
        bxs = [p[0] for p in bezier_pts]
        bys = [p[1] for p in bezier_pts]
        bw = max(bxs) - min(bxs)
        bh = max(bys) - min(bys)
        if bw < 0.5 or bh < 0.5:
            return None
        b_area   = bw * bh
        b_aspect = (bw / bh) if bh > 0 else 999
        if b_area < MIN_CONE_AREA or b_area > MAX_CONE_AREA:
            return None
        if b_aspect > MAX_ASPECT or b_aspect < 1 / MAX_ASPECT:
            return None
        cx = sum(bxs) / len(bxs)
        cy = sum(bys) / len(bys)
        return "standing", cx, cy, None

    # Non-circle shapes: use full bounding box for filtering.
    area   = w * h
    aspect = (w / h) if h > 0 else 999
    if aspect > MAX_ASPECT or aspect < 1 / MAX_ASPECT:
        return None
    if area < MIN_CONE_AREA or area > MAX_CONE_AREA:
        return None

    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2

    if n_line >= 3:
        # Closed line-segment polygon (triangle = 3, arrow = 8) → pointer cone.
        verts = get_all_vertices(items)
        tip_angle = compute_tip_angle(verts, cx, cy)
        return "pointer", cx, cy, tip_angle

    if n_line == 2:
        # Some Illustrator PDFs export pointer triangles as two nl=2 sub-paths
        # (the triangle split in half) rather than one nl=3 closed path.
        # Accept if the 3 unique vertices form a non-degenerate triangle
        # (triangle area ≥ MIN_CONE_AREA / 3 to exclude open course-outline stubs).
        verts = get_all_vertices(items)
        if len(verts) == 3:
            (x0, y0), (x1, y1), (x2, y2) = verts
            tri_area = abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) / 2.0
            if tri_area >= MIN_CONE_AREA / 3.0:
                tip_angle = compute_tip_angle(verts, cx, cy)
                return "pointer", cx, cy, tip_angle

    if n_rect >= 1:
        # Rectangle item — a filled-rect pointer symbol.
        # No tip angle derivable from shape alone; assign_pointer_facing will handle it.
        return "pointer", cx, cy, None


# ---------------------------------------------------------------------------
# Course-outline texture
# ---------------------------------------------------------------------------

def _bezier_pts(p0, p1, p2, p3, steps=20):
    """Sample cubic Bézier at `steps` equally-spaced t values."""
    result = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3*p0[0] + 3*u**2*t*p1[0] + 3*u*t**2*p2[0] + t**3*p3[0]
        y = u**3*p0[1] + 3*u**2*t*p1[1] + 3*u*t**2*p2[1] + t**3*p3[1]
        result.append((x, y))
    return result


def _is_course_stroke(color):
    """Return True if color looks like the grey course-path stroke.

    Course lines in these PDFs are drawn in medium grey (~0.58), not near-black.
    Accept channels in 0.35–0.80 (not too dark, not too light) with all channels
    close together (grey, not a hue).
    """
    if color is None:
        return False
    r, g, b = color[0], color[1], color[2]
    return (0.35 < r < 0.80 and 0.35 < g < 0.80 and 0.35 < b < 0.80
            and max(r, g, b) - min(r, g, b) < 0.20)


def extract_course_lines(drawings, page, cx_centroid, cy_centroid, m_per_pt):
    """Extract course-path line segments from PDF drawings, in Blender metre space.

    Returns list of dicts: {"type": "l"|"c", "pts": [(x, y), ...]}
    """
    pr = page.rect

    segments = []
    for d in drawings:
        color = d.get("color")
        if not _is_course_stroke(color):
            continue
        # Must NOT have a dark fill (excludes cone shapes)
        fill = d.get("fill")
        if fill is not None and (fill[0] < DARK_THRESHOLD and fill[1] < DARK_THRESHOLD
                                 and fill[2] < DARK_THRESHOLD):
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        rw = rect.x1 - rect.x0
        rh = rect.y1 - rect.y0
        area = rw * rh
        if area < MIN_CONE_AREA:
            continue
        # Path centroid must be within page rect
        cx = (rect.x0 + rect.x1) / 2
        cy = (rect.y0 + rect.y1) / 2
        if not (pr.x0 <= cx <= pr.x1 and pr.y0 <= cy <= pr.y1):
            continue

        for it in d.get("items", []):
            if it[0] == "l":
                _, p1, p2 = it
                pts = [
                    ((p1.x - cx_centroid) * m_per_pt, -(p1.y - cy_centroid) * m_per_pt),
                    ((p2.x - cx_centroid) * m_per_pt, -(p2.y - cy_centroid) * m_per_pt),
                ]
                segments.append({"type": "l", "pts": pts})
            elif it[0] == "c":
                _, p1, p2, p3, p4 = it
                pts = [
                    ((p1.x - cx_centroid) * m_per_pt, -(p1.y - cy_centroid) * m_per_pt),
                    ((p2.x - cx_centroid) * m_per_pt, -(p2.y - cy_centroid) * m_per_pt),
                    ((p3.x - cx_centroid) * m_per_pt, -(p3.y - cy_centroid) * m_per_pt),
                    ((p4.x - cx_centroid) * m_per_pt, -(p4.y - cy_centroid) * m_per_pt),
                ]
                segments.append({"type": "c", "pts": pts})

    return segments


def render_course_texture(segments, bounds):
    """Render course-path segments as a white-on-black grayscale PIL Image.

    Returns (image, px_per_m_used).
    """
    import math as _math
    from PIL import Image, ImageDraw

    xmin = bounds["xmin"]
    xmax = bounds["xmax"]
    ymin = bounds["ymin"]
    ymax = bounds["ymax"]

    width_m  = xmax - xmin
    height_m = ymax - ymin

    px_per_m = COURSE_LINE_PX_PER_M
    w_px = _math.ceil(width_m  * px_per_m)
    h_px = _math.ceil(height_m * px_per_m)

    # Cap to max dimension
    longest = max(w_px, h_px)
    if longest > COURSE_LINE_MAX_PX:
        px_per_m = px_per_m * COURSE_LINE_MAX_PX / longest
        w_px = _math.ceil(width_m  * px_per_m)
        h_px = _math.ceil(height_m * px_per_m)

    line_px = max(1, round(px_per_m * COURSE_LINE_WIDTH_M))

    img  = Image.new("L", (w_px, h_px), 0)
    draw = ImageDraw.Draw(img)

    def to_px(bx, by):
        px = (bx - xmin) * px_per_m
        py = (ymax - by) * px_per_m   # flip Y
        return px, py

    for seg in segments:
        if seg["type"] == "l":
            p1, p2 = seg["pts"]
            draw.line([to_px(*p1), to_px(*p2)], fill=255, width=line_px)
        elif seg["type"] == "c":
            p0, p1, p2, p3 = seg["pts"]
            sampled = _bezier_pts(p0, p1, p2, p3)
            pixel_pts = [to_px(*p) for p in sampled]
            for i in range(len(pixel_pts) - 1):
                draw.line([pixel_pts[i], pixel_pts[i + 1]], fill=255, width=line_px)

    return img, px_per_m


# ---------------------------------------------------------------------------
# Text-glyph false-positive filter
# ---------------------------------------------------------------------------

def get_text_bboxes(page, pad=2.0):
    """Return list of (x0,y0,x1,y1) bounding boxes for every text span.

    In Illustrator-exported PDFs, text is stored as outlined vector paths, so
    glyphs (especially '0', 'o', circular letters) can be misclassified as
    standing cones.  Any cone candidate whose centroid falls inside one of
    these boxes is a glyph and should be discarded.

    pad: expand each bbox by this many pts to catch centroids near the edge.
    """
    bboxes = []
    try:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if not span.get("text", "").strip():
                        continue
                    b = span["bbox"]  # (x0, y0, x1, y1)
                    bboxes.append((b[0]-pad, b[1]-pad, b[2]+pad, b[3]+pad))
    except Exception:
        pass
    return bboxes


def filter_text_glyphs(candidates, text_bboxes):
    """Remove candidates whose centroid (pdf_x, pdf_y) falls inside a text bbox."""
    if not text_bboxes:
        return candidates
    result = []
    for c in candidates:
        x, y = c["pdf_x"], c["pdf_y"]
        in_text = any(x0 <= x <= x1 and y0 <= y <= y1
                      for (x0, y0, x1, y1) in text_bboxes)
        if not in_text:
            result.append(c)
    return result


# ---------------------------------------------------------------------------
# Start / Finish detection (colored bars + text fallback)
# ---------------------------------------------------------------------------

# Colored bar fill thresholds (0–1 float RGB).
# Start bar: green-ish  — high G, lower R and B
# Finish bar: red/pink  — high R, lower G and B
_START_BAR_G_MIN  = 0.55   # green channel minimum
_START_BAR_R_MAX  = 0.80   # red   channel maximum (allow yellow-green)
_START_BAR_B_MAX  = 0.70   # blue  channel maximum
_FINISH_BAR_R_MIN = 0.70   # red   channel minimum
_FINISH_BAR_G_MAX = 0.85   # green channel maximum (pink/salmon allowed)
_FINISH_BAR_B_MAX = 0.85   # blue  channel maximum

# Bars are large compared to cones — min area in pt²
_BAR_MIN_AREA = 500.0


def _is_start_bar_color(fill):
    """Return True if fill (r, g, b floats 0–1) looks like the green start bar."""
    if fill is None:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    return (g >= _START_BAR_G_MIN and g > r and g > b
            and r <= _START_BAR_R_MAX and b <= _START_BAR_B_MAX)


def _is_finish_bar_color(fill):
    """Return True if fill looks like the red/pink finish bar."""
    if fill is None:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    return (r >= _FINISH_BAR_R_MIN and r > b
            and g <= _FINISH_BAR_G_MAX and b <= _FINISH_BAR_B_MAX
            and r > g + 0.05)


def detect_start_finish(page, drawings):
    """Return list of dicts with type, pdf_x, pdf_y for Start/Finish markers.

    `drawings` must be pre-filtered to the page's visible rect.

    Detection order (first match wins per type):
      1. Colored bars: green-filled shape → timing_start,
                       red/pink-filled shape → timing_end.
      2. Colored text fallback: green "Start" text, red "Finish" text.
    """
    results = []

    # --- 1. Colored bar shapes: keep only the largest per type ---
    # `drawings` is pre-filtered to the page's visible rect by the caller.
    best_start  = None   # (area, cx, cy)
    best_finish = None
    for d in drawings:
        fill = d.get("fill")
        if fill is None:
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        area = (rect.x1 - rect.x0) * (rect.y1 - rect.y0)
        if area < _BAR_MIN_AREA:
            continue
        cx = (rect.x0 + rect.x1) / 2
        cy = (rect.y0 + rect.y1) / 2
        if _is_start_bar_color(fill):
            if best_start is None or area > best_start[0]:
                best_start = (area, cx, cy)
        elif _is_finish_bar_color(fill):
            if best_finish is None or area > best_finish[0]:
                best_finish = (area, cx, cy)

    if best_start:
        results.append({"type": "timing_start", "pdf_x": best_start[1], "pdf_y": best_start[2], "source": "bar"})
    if best_finish:
        results.append({"type": "timing_end", "pdf_x": best_finish[1], "pdf_y": best_finish[2], "source": "bar"})

    # --- 2. Text fallback (only if bars didn't find both) ---
    has_start  = any(r["type"] == "timing_start" for r in results)
    has_finish = any(r["type"] == "timing_end"   for r in results)
    if has_start and has_finish:
        return results

    try:
        text_dict = page.get_text("dict")
    except Exception:
        return results

    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                color = span.get("color", 0)
                r = (color >> 16) & 255
                g = (color >> 8)  & 255
                b = color & 255
                origin = span.get("origin", (0, 0))
                px, py = float(origin[0]), float(origin[1])

                is_start  = "start"  in text.lower()
                is_finish = "finish" in text.lower()
                is_green  = g > 100 and r < 100 and b < 100
                is_red    = r > 150 and g < 100 and b < 100

                if is_start and is_green and not has_start:
                    results.append({"type": "timing_start", "pdf_x": px, "pdf_y": py, "source": "text"})
                    has_start = True
                elif is_finish and is_red and not has_finish:
                    results.append({"type": "timing_end", "pdf_x": px, "pdf_y": py, "source": "text"})
                    has_finish = True

    return results


# ---------------------------------------------------------------------------
# Coordinate transform
# ---------------------------------------------------------------------------

def pdf_to_blender(pdf_x, pdf_y, cx_centroid, cy_centroid, m_per_pt):
    """Convert PDF point coordinates to Blender world-space metres.

    Origin = centroid of all cones.  Y axis is flipped (pymupdf y-down → Blender y-up).
    """
    bx = (pdf_x - cx_centroid) * m_per_pt
    by = -(pdf_y - cy_centroid) * m_per_pt
    return round(bx, 3), round(by, 3)


# ---------------------------------------------------------------------------
# Timing cone tagging
# ---------------------------------------------------------------------------

def tag_timing_cones(standing, text_detections, m_per_pt, cx, cy,
                     radius_m=10.0, bar_radius_m=80.0):
    """Move standing cones near Start/Finish labels into timing_start / timing_end lists.

    Finds up to 2 nearest standing cones within radius_m for each text label,
    or bar_radius_m for bar-sourced detections.

    If no standing cones are found within range for a bar detection, a synthetic
    cone is inserted at the bar's centroid position so the timing position is
    preserved even when gate cones are absent from that page.

    Returns (remaining_standing, timing_start_list, timing_end_list).
    """
    if not text_detections:
        return standing, [], []

    timing_start = []
    timing_end   = []
    tagged_idxs  = set()

    for det in text_detections:
        tbx, tby = pdf_to_blender(det["pdf_x"], det["pdf_y"], cx, cy, m_per_pt)
        r = bar_radius_m if det.get("source") == "bar" else radius_m

        dists = []
        for i, sc in enumerate(standing):
            d = math.hypot(sc["bx"] - tbx, sc["by"] - tby)
            dists.append((d, i))
        dists.sort()

        n_tagged = 0
        for d, i in dists[:2]:
            if d <= r and i not in tagged_idxs:
                tagged_idxs.add(i)
                sc = dict(standing[i])
                sc["type"] = det["type"]
                if det["type"] == "timing_start":
                    timing_start.append(sc)
                else:
                    timing_end.append(sc)
                n_tagged += 1

        # Bar fallback: no cone found — insert synthetic marker at bar position
        if n_tagged == 0 and det.get("source") == "bar":
            synthetic = {
                "bx": round(tbx, 3), "by": round(tby, 3),
                "type": det["type"], "size": 1,
            }
            if det["type"] == "timing_start":
                timing_start.append(synthetic)
            else:
                timing_end.append(synthetic)

    remaining = [sc for i, sc in enumerate(standing) if i not in tagged_idxs]
    return remaining, timing_start, timing_end


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(pdf_path, page_idx, out_path,
        preview_path=None, map_path=None, snap_pointers=True,
        snap_radius_m=POINTER_SNAP_ANCHOR_RADIUS_M,
        course_path=None):

    print(f"Opening {pdf_path} page {page_idx} ...", file=sys.stderr)
    doc  = fitz.open(pdf_path)
    if page_idx < 1 or page_idx > len(doc):
        print(f"  ERROR: page {page_idx} out of range (1–{len(doc)})", file=sys.stderr)
        sys.exit(1)
    page = doc[page_idx - 1]   # convert 1-indexed to 0-indexed
    print(f"  Page rect: {page.rect}", file=sys.stderr)

    drawings = page.get_drawings()
    print(f"  Drawings: {len(drawings)}", file=sys.stderr)

    # Filter drawings to the visible page area.
    # PDFs with crop-box pages (e.g. multi-page Illustrator exports) return all
    # drawings from the shared mediabox via get_drawings(); those outside the
    # page rect must be discarded.
    pr = page.rect
    drawings = [d for d in drawings
                if d.get("rect") is not None
                and pr.x0 <= (d["rect"].x0 + d["rect"].x1) / 2 <= pr.x1
                and pr.y0 <= (d["rect"].y0 + d["rect"].y1) / 2 <= pr.y1]
    print(f"  Drawings in page rect: {len(drawings)}", file=sys.stderr)

    # --- Scale (1 pt = 1 ft, confirmed by source) ---
    m_per_pt = M_PER_PT
    print(f"  Scale: {m_per_pt} m/pt (1 pt = 1 ft)", file=sys.stderr)

    # --- Cone extraction ---
    raw_standing = []
    raw_pointer  = []

    for d in drawings:
        result = classify_candidate(d)
        if result is None:
            continue
        kind, cx, cy, tip_angle = result
        if kind == "standing":
            raw_standing.append({"pdf_x": cx, "pdf_y": cy, "tip_angle": None})
        else:
            raw_pointer.append({"pdf_x": cx, "pdf_y": cy, "tip_angle": tip_angle})

    print(f"  Raw candidates: {len(raw_standing)} standing, {len(raw_pointer)} pointer",
          file=sys.stderr)

    # --- Filter text-glyph false positives ---
    text_bboxes = get_text_bboxes(page)
    n_before = len(raw_standing) + len(raw_pointer)
    raw_standing = filter_text_glyphs(raw_standing, text_bboxes)
    raw_pointer  = filter_text_glyphs(raw_pointer,  text_bboxes)
    n_after = len(raw_standing) + len(raw_pointer)
    if n_before != n_after:
        print(f"  Text filter: removed {n_before - n_after} glyph false positives "
              f"({len(text_bboxes)} text spans)", file=sys.stderr)

    # --- Dot-cluster standing cones (micro-triangle representation) ---
    dot_clusters = detect_dot_clusters(drawings)
    if dot_clusters:
        # Filter clusters that overlap existing standing candidates (same cell)
        existing_pts = {(round(c["pdf_x"]/DOT_CLUSTER_RADIUS_PT),
                         round(c["pdf_y"]/DOT_CLUSTER_RADIUS_PT))
                        for c in raw_standing}
        added = 0
        for dc in dot_clusters:
            key = (round(dc["pdf_x"]/DOT_CLUSTER_RADIUS_PT),
                   round(dc["pdf_y"]/DOT_CLUSTER_RADIUS_PT))
            if key not in existing_pts:
                raw_standing.append({"pdf_x": dc["pdf_x"], "pdf_y": dc["pdf_y"],
                                     "tip_angle": None})
                existing_pts.add(key)
                added += 1
        if added:
            print(f"  Dot clusters: +{added} standing cones", file=sys.stderr)

    if not raw_standing and not raw_pointer:
        print("  WARNING: no cone candidates detected — check dark-fill threshold",
              file=sys.stderr)

    # --- Centroid (PDF coords) ---
    all_raw = raw_standing + raw_pointer
    if all_raw:
        cx_centroid = float(np.mean([c["pdf_x"] for c in all_raw]))
        cy_centroid = float(np.mean([c["pdf_y"] for c in all_raw]))
    else:
        cx_centroid = page.rect.width  / 2
        cy_centroid = page.rect.height / 2

    print(f"  Centroid: ({cx_centroid:.1f}, {cy_centroid:.1f}) pt", file=sys.stderr)

    transform = {
        "type":  "scale",
        "scale": round(m_per_pt, 6),
        "ox":    round(-m_per_pt * cx_centroid, 4),
        "oy":    round( m_per_pt * cy_centroid, 4),
    }

    # --- Convert to Blender coords ---
    def to_bl(pdf_x, pdf_y, cone_type, size=1, **extra):
        bx, by = pdf_to_blender(pdf_x, pdf_y, cx_centroid, cy_centroid, m_per_pt)
        entry = {"bx": bx, "by": by, "type": cone_type, "size": size}
        entry.update(extra)
        return entry

    standing = [to_bl(c["pdf_x"], c["pdf_y"], "standing") for c in raw_standing]
    pointers = [to_bl(c["pdf_x"], c["pdf_y"], "pointer",
                      tip_from_pdf=c["tip_angle"])
                for c in raw_pointer]

    # --- Start / Finish ---
    text_dets = detect_start_finish(page, drawings)
    print(f"  Text detections: {text_dets}", file=sys.stderr)
    standing, timing_start, timing_end = tag_timing_cones(
        standing, text_dets, m_per_pt, cx_centroid, cy_centroid,
    )

    # --- Pointer orientation (toward nearest standing cone) ---
    assign_pointer_facing(pointers, standing)

    # --- Snap pointers to physically correct positions ---
    if snap_pointers:
        timing_all = timing_start + timing_end
        n_snapped = snap_pointers_to_standing(
            pointers, standing,
            anchor_radius_m=snap_radius_m,
            timing_cones=timing_all if timing_all else None,
        )
        print(f"  Snapped {n_snapped}/{len(pointers)} pointers "
              f"(radius={snap_radius_m}m)", file=sys.stderr)
    else:
        print("  Pointer snap disabled", file=sys.stderr)

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
        "pointer_source": "pdf",
        "n_standing":     len(standing),
        "n_pointer":      len(pointers),
        "n_timing_start": len(timing_start),
        "n_timing_end":   len(timing_end),
        "n_gcp":          0,
        "bounds":         bounds,
        "standing":       standing,
        "pointers":       pointers,
        "timing_start":   timing_start,
        "timing_end":     timing_end,
        "gcp":            [],
    }

    # Strip internal-only fields before writing
    def strip_internal(cone):
        return {k: v for k, v in cone.items() if k != "tip_from_pdf"}

    result["standing"]     = [strip_internal(c) for c in result["standing"]]
    result["pointers"]     = [strip_internal(c) for c in result["pointers"]]
    result["timing_start"] = [strip_internal(c) for c in result["timing_start"]]
    result["timing_end"]   = [strip_internal(c) for c in result["timing_end"]]

    with open(out_path, "w") as f:
        json.dump(native(result), f, indent=2)
    print(f"  Wrote: {out_path}", file=sys.stderr)
    print(f"  Counts: {len(standing)} standing, {len(pointers)} pointer, "
          f"{len(timing_start)} t-start, {len(timing_end)} t-end",
          file=sys.stderr)

    # --- Course outline texture ---
    if course_path and bounds:
        segments = extract_course_lines(drawings, page, cx_centroid, cy_centroid, m_per_pt)
        print(f"  Course segments: {len(segments)}", file=sys.stderr)
        if segments:
            # Expand bounds to cover segment endpoints (course may extend beyond cones)
            seg_xs = [pt[0] for s in segments for pt in s["pts"]]
            seg_ys = [pt[1] for s in segments for pt in s["pts"]]
            tex_bounds = {
                "xmin": round(min(bounds["xmin"], min(seg_xs)) - 1.0, 1),
                "xmax": round(max(bounds["xmax"], max(seg_xs)) + 1.0, 1),
                "ymin": round(min(bounds["ymin"], min(seg_ys)) - 1.0, 1),
                "ymax": round(max(bounds["ymax"], max(seg_ys)) + 1.0, 1),
            }
            img, px_per_m_used = render_course_texture(segments, tex_bounds)
            img.save(course_path)
            sidecar_path = str(course_path).replace(".png", ".json")
            sidecar = {
                "xmin":      tex_bounds["xmin"],
                "xmax":      tex_bounds["xmax"],
                "ymin":      tex_bounds["ymin"],
                "ymax":      tex_bounds["ymax"],
                "px_per_m":  round(px_per_m_used, 4),
                "width_px":  img.width,
                "height_px": img.height,
            }
            with open(sidecar_path, "w") as f:
                json.dump(sidecar, f, indent=2)
            print(f"  Course texture: {course_path} ({img.width}×{img.height} px, "
                  f"{px_per_m_used:.2f} px/m)", file=sys.stderr)
        else:
            print("  WARNING: no course segments found", file=sys.stderr)

    # --- Preview ---
    if preview_path:
        _render_preview(page, result, raw_standing, raw_pointer,
                        cx_centroid, cy_centroid, m_per_pt, preview_path)

    if map_path:
        _export_map(page, map_path)

    return result


def _export_map(page, map_path):
    """Render the PDF page at 72 DPI (1 pixel = 1 PDF point = 1 ft) and save as PNG.

    This image can be loaded as a background in the frontend editor; the JSON
    coordinates produced by run() map cone positions 1:1 onto image pixels.
    """
    pix = page.get_pixmap(dpi=72)
    pix.save(map_path)
    print(f"  Map: {map_path} ({pix.width}×{pix.height} px)", file=sys.stderr)


def _render_preview(page, result, raw_standing, raw_pointer,
                    cx_centroid, cy_centroid, m_per_pt, preview_path):
    from PIL import Image, ImageDraw

    dpi   = 150
    scale = dpi / 72.0   # PDF points → pixels
    pix   = page.get_pixmap(dpi=dpi)
    img   = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    draw  = ImageDraw.Draw(img)

    def bl_to_px(bx, by):
        pdf_x = bx / m_per_pt + cx_centroid
        pdf_y = -(by / m_per_pt) + cy_centroid
        return pdf_x * scale, pdf_y * scale

    R_STAND = max(3, int(4 * scale))
    R_PTR   = max(3, int(5 * scale))
    R_TIME  = max(4, int(6 * scale))

    for c in result["standing"]:
        px, py = bl_to_px(c["bx"], c["by"])
        draw.ellipse([px-R_STAND, py-R_STAND, px+R_STAND, py+R_STAND],
                     outline=(220, 40, 40), width=2)

    for c in result["pointers"]:
        px, py = bl_to_px(c["bx"], c["by"])
        draw.ellipse([px-R_PTR, py-R_PTR, px+R_PTR, py+R_PTR],
                     outline=(0, 200, 220), width=2)
        fdeg = c.get("facing_deg")
        if fdeg is not None:
            rad = math.radians(fdeg)
            ex = px + R_PTR * 2.5 * math.cos(rad)
            ey = py - R_PTR * 2.5 * math.sin(rad)  # screen y is down
            draw.line([px, py, ex, ey], fill=(0, 200, 220), width=1)

    for c in result["timing_start"]:
        px, py = bl_to_px(c["bx"], c["by"])
        draw.rectangle([px-R_TIME, py-R_TIME, px+R_TIME, py+R_TIME],
                       outline=(0, 200, 60), width=3)

    for c in result["timing_end"]:
        px, py = bl_to_px(c["bx"], c["by"])
        draw.rectangle([px-R_TIME, py-R_TIME, px+R_TIME, py+R_TIME],
                       outline=(220, 40, 40), width=3)

    img.save(preview_path)
    print(f"  Preview: {preview_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pdf",        required=True, help="Input PDF path")
    p.add_argument("--page",       type=int, default=1,
                   help="1-indexed page number (default: 1)")
    p.add_argument("--out",        required=True, help="Output JSON path")
    p.add_argument("--map",        default=None,  help="Output clean map PNG at 72 DPI (1px=1pt=1ft)")
    p.add_argument("--preview",    default=None,  help="Optional annotated PNG output")
    p.add_argument("--course",     default=None,  help="Output course outline texture PNG path")
    p.add_argument("--no-snap-pointers", action="store_true", default=False,
                   help="Disable snapping pointer cones to 3-inch increments")
    p.add_argument("--snap-radius", type=float, default=POINTER_SNAP_ANCHOR_RADIUS_M,
                   metavar="M",
                   help=f"Max anchor-pointer distance for snapping "
                        f"(default: {POINTER_SNAP_ANCHOR_RADIUS_M}m)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        pdf_path=args.pdf,
        page_idx=args.page,
        out_path=args.out,
        preview_path=args.preview,
        map_path=args.map,
        course_path=args.course,
        snap_pointers=not args.no_snap_pointers,
        snap_radius_m=args.snap_radius,
    )
