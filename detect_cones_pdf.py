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
PAGE_MARGIN_PT  = 20.0   # pt  — discard candidates within this margin of any page edge

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
    """Angle (degrees CCW from +X, Blender coords) of the pointer's tip direction.

    The vertex centroid of an arrow/triangle shape is biased toward the base
    (more vertices live there).  The tip is therefore the vertex farthest from
    the vertex centroid, which is perpendicular to the base for symmetric shapes
    and sensibly interpolated for asymmetric ones.

    Y is negated because pymupdf uses y-down but Blender uses y-up.
    """
    if not verts or len(verts) < 3:
        return None

    vx = sum(v[0] for v in verts) / len(verts)
    vy = sum(v[1] for v in verts) / len(verts)

    tip = max(verts, key=lambda v: (v[0] - vx) ** 2 + (v[1] - vy) ** 2)
    dx = tip[0] - vx
    dy = tip[1] - vy
    return round(math.degrees(math.atan2(-dy, dx)), 1)   # flip Y for Blender


def classify_candidate(d):
    """Classify one candidate drawing.

    Returns ('standing'|'pointer', cx, cy, tip_angle_or_None, area_pt2, n_line) or None.
    area_pt2 is the bounding-box area; n_line is used for the line-count dominance filter.
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
        return "standing", cx, cy, None, b_area, 0

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
        return "pointer", cx, cy, tip_angle, area, n_line

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
                return "pointer", cx, cy, tip_angle, area, n_line

    if n_rect >= 1 and n_line >= 1:
        # Rectangle combined with line segments — some PDFs encode the pointer base
        # as a rect + attached lines.  Bare rectangles with no lines are border
        # markers or scale-bar elements, not cone symbols.
        return "pointer", cx, cy, None, area, n_line


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

def has_outlined_text(page, drawings, match_radius_factor=0.8, min_matches=5):
    """Return True if this page has text rendered as outlined vector paths.

    In some Illustrator PDFs, text is exported as outlined (filled) paths rather
    than as a live text stream.  Circular characters ('0', 'O', 'o') then appear
    in the drawings list and can be misclassified as standing cones.

    Detection heuristic: check how many dark circles in the drawings list have a
    circular character ('0', 'O', 'o') in the text stream within match_radius_factor
    × font_size.  If fewer than min_matches, the text is not outlined and the
    glyph filter should be skipped to avoid falsely removing real cone circles that
    happen to sit near numeric cone labels.
    """
    DARK = 0.35
    circle_positions = []
    for d in drawings:
        fill = d.get("fill")
        if fill is None or not all(c < DARK for c in fill[:3]):
            continue
        items = d.get("items", [])
        if sum(1 for it in items if it[0] == "c") < 2:
            continue
        rect = d["rect"]
        circle_positions.append(((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2))

    if not circle_positions:
        return False

    try:
        text_chars = []
        for block in page.get_text("rawdict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    sz = span.get("size", 0)
                    for ch in span.get("chars", []):
                        if ch.get("c", "") in "0Oo":
                            orig = ch.get("origin", (0, 0))
                            text_chars.append((orig[0], orig[1], sz))
    except Exception:
        return False

    if not text_chars:
        return False

    matches = 0
    for cx, cy in circle_positions:
        for px, py, sz in text_chars:
            if math.hypot(cx - px, cy - py) < sz * match_radius_factor:
                matches += 1
                break

    return matches >= min_matches


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
# A timing bar is thin in one direction (it's a stripe, not a rectangle).
# Reject filled shapes where BOTH dimensions exceed this — those are background fills.
_BAR_MAX_MIN_DIM = 20.0   # pt — if min(width,height) > this, it's not a bar


def _is_start_bar_color(rgb):
    """Return True if rgb (r, g, b floats 0–1) looks like the green start bar."""
    if rgb is None:
        return False
    r, g, b = rgb[0], rgb[1], rgb[2]
    return (g >= _START_BAR_G_MIN and g > r and g > b
            and r <= _START_BAR_R_MAX and b <= _START_BAR_B_MAX)


def _is_finish_bar_color(rgb):
    """Return True if rgb looks like the red/pink finish bar."""
    if rgb is None:
        return False
    r, g, b = rgb[0], rgb[1], rgb[2]
    return (r >= _FINISH_BAR_R_MIN and r > b
            and g <= _FINISH_BAR_G_MAX and b <= _FINISH_BAR_B_MAX
            and r > g + 0.05)


def _extract_stroke_bar_endpoints(drawings):
    """Find colored stroke bars (green/red lines) and return their endpoints.

    Returns (start_endpoints, finish_endpoints) where each is either
    ((x0,y0), (x1,y1)) or None.  Bars are colored STROKE paths (fill=None)
    with a single line item — the two endpoints are the LEFT/RIGHT marker positions.
    """
    best_start  = None   # (area/length, (x0,y0), (x1,y1))
    best_finish = None

    for d in drawings:
        color = d.get("color")
        if color is None:
            continue
        if d.get("fill") is not None:
            continue   # skip filled shapes — we want strokes only

        items = d.get("items", [])
        # Collect all line endpoints; each 'l' item = (type, p1, p2)
        pts = []
        for it in items:
            if it[0] == "l":
                pts.append((it[1].x, it[1].y))
                pts.append((it[2].x, it[2].y))

        if len(pts) < 2:
            continue

        x0, y0 = pts[0]
        x1, y1 = pts[-1]
        length  = math.hypot(x1 - x0, y1 - y0)
        if length < 20:   # too short to be a timing bar
            continue

        if _is_start_bar_color(color):
            if best_start is None or length > best_start[0]:
                best_start = (length, (x0, y0), (x1, y1))
        elif _is_finish_bar_color(color):
            if best_finish is None or length > best_finish[0]:
                best_finish = (length, (x0, y0), (x1, y1))

    se = (best_start[1],  best_start[2])  if best_start  else None
    fe = (best_finish[1], best_finish[2]) if best_finish else None
    return se, fe


def detect_start_finish(page, drawings):
    """Return list of dicts with type, pdf_x, pdf_y for Start/Finish markers.

    `drawings` must be pre-filtered to the page's visible rect.

    Detection order (first match wins per type):
      1. Colored text labels: green "Start", red "Finish".
      2. Colored bar strokes: green/red stroke paths — endpoints stored as gate.
      3. Colored bar fills: green/red filled shapes — center only, no gate.
    """
    results = []
    has_start  = False
    has_finish = False

    # --- 1. Colored text labels: "Start" (green) and "Finish" (red) ---
    try:
        text_dict = page.get_text("dict")
    except Exception:
        text_dict = {"blocks": []}

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
                is_green  = g > 100 and g > r and g > b
                is_red    = r > 150 and g < 100 and b < 100

                if is_start and is_green and not has_start:
                    results.append({"type": "timing_start", "pdf_x": px, "pdf_y": py, "source": "text"})
                    has_start = True
                elif is_finish and is_red and not has_finish:
                    results.append({"type": "timing_end", "pdf_x": px, "pdf_y": py, "source": "text"})
                    has_finish = True

    # --- 2. Colored bar strokes: extract endpoints as gate left/right ---
    stroke_start_ep, stroke_finish_ep = _extract_stroke_bar_endpoints(drawings)

    if stroke_start_ep and not has_start:
        (x0, y0), (x1, y1) = stroke_start_ep
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        results.append({"type": "timing_start", "pdf_x": cx, "pdf_y": cy,
                        "source": "bar",
                        "gate_a": (x0, y0), "gate_b": (x1, y1)})
        has_start = True
        print(f"  Start stroke bar endpoints: ({x0:.1f},{y0:.1f}) → ({x1:.1f},{y1:.1f})",
              file=sys.stderr)
    elif stroke_start_ep:
        # Text already found start — attach bar endpoints only if bar is near text label
        (x0, y0), (x1, y1) = stroke_start_ep
        bar_cx, bar_cy = (x0 + x1) / 2, (y0 + y1) / 2
        for r in results:
            if r["type"] == "timing_start":
                dist = math.hypot(bar_cx - r["pdf_x"], bar_cy - r["pdf_y"])
                if dist < 200:
                    r["gate_a"] = stroke_start_ep[0]
                    r["gate_b"] = stroke_start_ep[1]
                else:
                    print(f"  Start bar at ({bar_cx:.0f},{bar_cy:.0f}) is {dist:.0f}pt from "
                          f"text label — ignoring bar endpoints", file=sys.stderr)
                break

    if stroke_finish_ep and not has_finish:
        (x0, y0), (x1, y1) = stroke_finish_ep
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        results.append({"type": "timing_end", "pdf_x": cx, "pdf_y": cy,
                        "source": "bar",
                        "gate_a": (x0, y0), "gate_b": (x1, y1)})
        has_finish = True
        print(f"  Finish stroke bar endpoints: ({x0:.1f},{y0:.1f}) → ({x1:.1f},{y1:.1f})",
              file=sys.stderr)
    elif stroke_finish_ep:
        # Text already found finish — attach bar endpoints only if bar is near text label
        (x0, y0), (x1, y1) = stroke_finish_ep
        bar_cx, bar_cy = (x0 + x1) / 2, (y0 + y1) / 2
        for r in results:
            if r["type"] == "timing_end":
                dist = math.hypot(bar_cx - r["pdf_x"], bar_cy - r["pdf_y"])
                if dist < 200:
                    r["gate_a"] = stroke_finish_ep[0]
                    r["gate_b"] = stroke_finish_ep[1]
                else:
                    print(f"  Finish bar at ({bar_cx:.0f},{bar_cy:.0f}) is {dist:.0f}pt from "
                          f"text label — ignoring bar endpoints", file=sys.stderr)
                break

    # --- 3. Colored bar fills: fallback when no stroke bars found ---
    if not has_start or not has_finish:
        best_start  = None
        best_finish = None
        for d in drawings:
            fill = d.get("fill")
            if fill is None:
                continue
            rect = d.get("rect")
            if rect is None:
                continue
            w = rect.x1 - rect.x0
            h = rect.y1 - rect.y0
            area = w * h
            if area < _BAR_MIN_AREA:
                continue
            if min(w, h) > _BAR_MAX_MIN_DIM:
                continue
            cx = (rect.x0 + rect.x1) / 2
            cy = (rect.y0 + rect.y1) / 2
            if not has_start and _is_start_bar_color(fill):
                if best_start is None or area > best_start[0]:
                    best_start = (area, cx, cy)
            elif not has_finish and _is_finish_bar_color(fill):
                if best_finish is None or area > best_finish[0]:
                    best_finish = (area, cx, cy)

        if best_start:
            results.append({"type": "timing_start", "pdf_x": best_start[1], "pdf_y": best_start[2], "source": "bar"})
            has_start = True
        if best_finish:
            results.append({"type": "timing_end", "pdf_x": best_finish[1], "pdf_y": best_finish[2], "source": "bar"})
            has_finish = True

    # --- 3. Cone-number finish: always compute, use to validate/replace bar result ---
    cn_finish = _detect_finish_from_cone_numbers(page)
    if cn_finish:
        existing = next((r for r in results if r["type"] == "timing_end"), None)
        if existing is None:
            results.append(cn_finish)
        elif existing.get("source") == "bar":
            # Only validate/replace bar results — text labels are authoritative.
            d = math.hypot(existing["pdf_x"] - cn_finish["pdf_x"],
                           existing["pdf_y"] - cn_finish["pdf_y"])
            if d > 150:
                print(f"  Finish bar at ({existing['pdf_x']:.0f},{existing['pdf_y']:.0f}) "
                      f"is {d:.0f}pt from cone-number estimate "
                      f"({cn_finish['pdf_x']:.0f},{cn_finish['pdf_y']:.0f}) — "
                      f"using cone-number result", file=sys.stderr)
                results = [r for r in results if r["type"] != "timing_end"]
                results.append(cn_finish)

    return results


def _detect_finish_from_cone_numbers(page):
    """Infer finish gate position from cone number sequences.

    Solo Nationals courses number cones by section (100s = section 1 near start,
    500s/600s = last section near finish).  The finish gate sits where the
    highest-numbered section transitions into the finish chute — identified as
    the largest positional gap when last-section cones are projected onto their
    primary direction of travel.

    Returns a dict {'type':'timing_end', 'pdf_x':..., 'pdf_y':..., 'source':'cone_numbers'}
    or None if insufficient data.
    """
    labels = {}   # number → (x, y)
    try:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if txt.isdigit():
                        n = int(txt)
                        if 100 <= n <= 699:
                            x = (span["bbox"][0] + span["bbox"][2]) / 2
                            y = (span["bbox"][1] + span["bbox"][3]) / 2
                            labels[n] = (x, y)
    except Exception:
        return None

    if not labels:
        return None

    max_section = max(n // 100 for n in labels)
    if max_section < 2:
        return None

    last_cones = sorted(
        [(n, labels[n]) for n in labels if n // 100 == max_section],
        key=lambda t: t[0]
    )
    if len(last_cones) < 4:
        return None

    sec1_pts = [labels[n] for n in labels if n // 100 == 1]

    # Primary travel direction via PCA of last-section cone positions
    pts = np.array([c[1] for c in last_cones], dtype=float)
    pts_c = pts - pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts_c, full_matrices=False)
    travel = vt[0]   # principal axis (un-signed)

    # Project all last-section cones onto the travel axis and sort
    projections = pts_c @ travel
    order = np.argsort(projections)
    sorted_proj = projections[order]
    sorted_pts  = pts[order]

    # Find the gap that is flanked by section-1 cones on the other side of the
    # driving lane.  This distinguishes the finish gate gap (which has section-1
    # finish-chute cones nearby) from the bottom-of-course section transition
    # (which does not).
    gaps = np.diff(sorted_proj)
    best_gap_idx  = None
    best_gap_size = 0.0
    SEC1_PROXIMITY = 300.0   # pt — max distance to nearest sec-1 cone

    for i in range(len(gaps)):
        gx_candidate = (sorted_pts[i][0] + sorted_pts[i + 1][0]) / 2
        gy_candidate = (sorted_pts[i][1] + sorted_pts[i + 1][1]) / 2
        nearest_sec1 = min(
            (math.hypot(p[0] - gx_candidate, p[1] - gy_candidate) for p in sec1_pts),
            default=1e9
        )
        if nearest_sec1 < SEC1_PROXIMITY and gaps[i] > best_gap_size:
            best_gap_size = gaps[i]
            best_gap_idx  = i

    # If no sec-1-flanked gap found, fall back to largest overall gap
    if best_gap_idx is None:
        best_gap_idx  = int(np.argmax(gaps))
        best_gap_size = gaps[best_gap_idx]

    gx = (sorted_pts[best_gap_idx][0] + sorted_pts[best_gap_idx + 1][0]) / 2
    gy = (sorted_pts[best_gap_idx][1] + sorted_pts[best_gap_idx + 1][1]) / 2

    # Refine lateral position: average x/y between last-section cones near the
    # gap and section-1 chute cones on the other side
    last_near = [c[1] for c in last_cones if math.hypot(c[1][0]-gx, c[1][1]-gy) < 150]
    sec1_near = [p for p in sec1_pts    if math.hypot(p[0]-gx,     p[1]-gy)     < 150]
    if last_near and sec1_near:
        gx = (sum(p[0] for p in last_near) / len(last_near) +
              sum(p[0] for p in sec1_near)  / len(sec1_near)) / 2
        gy = (sum(p[1] for p in last_near) / len(last_near) +
              sum(p[1] for p in sec1_near)  / len(sec1_near)) / 2

    print(f"  Finish gate from cone numbers: section={max_section*100}s, "
          f"gap={best_gap_size:.0f}pt, gate≈({gx:.0f},{gy:.0f})", file=sys.stderr)

    return {"type": "timing_end", "pdf_x": gx, "pdf_y": gy, "source": "cone_numbers"}


def _detect_stage_position(page, gate_pdf_x=None, gate_pdf_y=None, max_dist_pt=300.0):
    """Return the centroid (pdf_x, pdf_y) of the lowest section-1 cone numbers near the start gate.

    Finds all section-1 cones (100-199) within max_dist_pt of the gate, then uses the
    cluster containing the lowest-numbered cone as the staging reference.  This avoids
    being confused by finish-chute cones (also section-1) which are far from the gate.

    If gate position is unknown, considers all section-1 cones globally.
    Returns None if fewer than 2 qualifying labels are found.
    """
    labels = {}
    try:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if txt.isdigit():
                        n = int(txt)
                        if 100 <= n <= 199:
                            x = (span["bbox"][0] + span["bbox"][2]) / 2
                            y = (span["bbox"][1] + span["bbox"][3]) / 2
                            labels[n] = (x, y)
    except Exception:
        return None

    if not labels:
        return None

    # Filter to those near the gate when we know the gate position
    if gate_pdf_x is not None:
        nearby = {n: (x, y) for n, (x, y) in labels.items()
                  if math.hypot(x - gate_pdf_x, y - gate_pdf_y) <= max_dist_pt}
        if len(nearby) >= 2:
            labels = nearby

    if len(labels) < 2:
        return None

    # Use the 5 lowest-numbered cones as the staging reference
    stage_cones = dict(sorted(labels.items())[:5])
    pts = list(stage_cones.values())
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    print(f"  Stage cones {sorted(stage_cones)}: pdf centroid ({cx:.0f},{cy:.0f})", file=sys.stderr)
    return (cx, cy)


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

def find_cones_by_label(page, drawings, cone_numbers, m_per_pt, cx_centroid, cy_centroid,
                        max_label_dist_pt=40.0):
    """Return Blender-coord dicts for standing cones identified by their numeric labels.

    Searches the PDF text stream for spans whose text contains any of the requested
    numbers (handles both individual labels like "109" and paired labels like "518,519").
    For each matched label, finds the nearest dark circle within max_label_dist_pt and
    returns it as a cone dict.

    cone_numbers: list of ints, e.g. [109, 110, 111, 112]
    """
    # Build a set of string forms for quick lookup
    targets = {str(n) for n in cone_numbers}

    # Collect label positions: map each target number → nearest label origin
    label_hits = {}
    try:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    orig = span.get("origin", (0, 0))
                    # Handle comma-separated paired labels ("518,519")
                    parts = [p.strip() for p in text.replace(" ", ",").split(",")]
                    for part in parts:
                        if part in targets and part not in label_hits:
                            label_hits[part] = (orig[0], orig[1])
    except Exception:
        pass

    if not label_hits:
        return []

    DARK = 0.35
    results = []
    used_circles = set()

    for num_str in sorted(label_hits, key=int):
        lx, ly = label_hits[num_str]
        best_d, best_d_info = 1e9, None
        for d in drawings:
            fill = d.get("fill")
            if fill is None or not all(c < DARK for c in fill[:3]):
                continue
            items = d.get("items", [])
            if sum(1 for it in items if it[0] == "c") < 2:
                continue
            rect = d["rect"]
            cx = (rect.x0 + rect.x1) / 2
            cy = (rect.y0 + rect.y1) / 2
            key = (round(cx, 1), round(cy, 1))
            if key in used_circles:
                continue
            dist = math.hypot(cx - lx, cy - ly)
            if dist < best_d:
                best_d, best_d_info = dist, (cx, cy, key)

        if best_d_info and best_d <= max_label_dist_pt:
            cx_c, cy_c, key = best_d_info
            used_circles.add(key)
            bx, by = pdf_to_blender(cx_c, cy_c, cx_centroid, cy_centroid, m_per_pt)
            results.append({"bx": bx, "by": by, "type": None, "size": 1,
                            "label": int(num_str), "source": "label"})

    return results


def tag_timing_cones(standing, text_detections, m_per_pt, cx, cy,
                     radius_m=10.0):
    """Place timing markers at bar/text positions and tag nearby standing cones.

    For bar-sourced detections the bar centroid is used directly as the marker
    position — the bar IS the timing line, so its centre is the right coordinate.
    For text-sourced detections (fallback when no bar was found), the 2 nearest
    standing cones within radius_m are pulled into the timing list instead, since
    the text label may be offset from the actual gate.

    Standing cones are never removed from the main list; timing entries are
    additional records alongside the regular cone data.

    Returns (standing_unchanged, timing_start_list, timing_end_list).
    """
    if not text_detections:
        return standing, [], []

    timing_start = []
    timing_end   = []

    for det in text_detections:
        tbx, tby = pdf_to_blender(det["pdf_x"], det["pdf_y"], cx, cy, m_per_pt)

        # Convert gate endpoints to world space if present
        gate_world = None
        if det.get("gate_a") and det.get("gate_b"):
            ax, ay = pdf_to_blender(det["gate_a"][0], det["gate_a"][1], cx, cy, m_per_pt)
            bx, by = pdf_to_blender(det["gate_b"][0], det["gate_b"][1], cx, cy, m_per_pt)
            gate_world = {"a": [ax, ay], "b": [bx, by]}

        if det.get("source") == "bar":
            marker = {"bx": round(tbx, 3), "by": round(tby, 3),
                      "type": det["type"], "size": 1, "source": "bar"}
            if gate_world:
                marker["gate"] = gate_world
            if det["type"] == "timing_start":
                timing_start.append(marker)
            else:
                timing_end.append(marker)
        else:
            # Text fallback: grab up to 2 nearest standing cones within radius_m.
            dists = sorted(
                (math.hypot(sc["bx"] - tbx, sc["by"] - tby), i)
                for i, sc in enumerate(standing)
            )
            tagged = []
            for d, i in dists[:2]:
                if d > radius_m:
                    break
                sc = dict(standing[i])
                sc["type"] = det["type"]
                sc["source"] = "text"
                tagged.append(sc)
            # Attach gate endpoints from stroke bar if available
            if gate_world and len(tagged) == 2:
                # Assign a/b endpoints to the two cones so Blender can use exact bar ends
                a = gate_world["a"]
                b = gate_world["b"]
                # Assign a to whichever cone is closer to endpoint a
                da = math.hypot(tagged[0]["bx"] - a[0], tagged[0]["by"] - a[1])
                db = math.hypot(tagged[0]["bx"] - b[0], tagged[0]["by"] - b[1])
                if da > db:
                    a, b = b, a
                tagged[0]["gate_end"] = a
                tagged[1]["gate_end"] = b
            elif gate_world:
                for sc in tagged:
                    sc["gate"] = gate_world
            for sc in tagged:
                if det["type"] == "timing_start":
                    timing_start.append(sc)
                else:
                    timing_end.append(sc)

    return standing, timing_start, timing_end


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(pdf_path, page_idx, out_path,
        preview_path=None, map_path=None, snap_pointers=False,
        snap_radius_m=POINTER_SNAP_ANCHOR_RADIUS_M,
        course_path=None,
        timing_start_cones=None,
        timing_end_cones=None,
        chalk_path=None,
        chalk_width_in=5.0,
        invert_gates=False):

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

    # Discard drawings within PAGE_MARGIN_PT of any page edge.
    # Border markers, scale bars, and spectator-zone fixtures cluster near the
    # edges; course cones are always well inside the page bounds.
    inner = fitz.Rect(pr.x0 + PAGE_MARGIN_PT, pr.y0 + PAGE_MARGIN_PT,
                      pr.x1 - PAGE_MARGIN_PT, pr.y1 - PAGE_MARGIN_PT)
    drawings = [d for d in drawings
                if inner.contains(fitz.Point((d["rect"].x0 + d["rect"].x1) / 2,
                                             (d["rect"].y0 + d["rect"].y1) / 2))]
    print(f"  After page-margin filter ({PAGE_MARGIN_PT:.0f} pt): {len(drawings)}", file=sys.stderr)

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
        kind, cx, cy, tip_angle, area_pt2, nl = result
        if kind == "standing":
            raw_standing.append({"pdf_x": cx, "pdf_y": cy, "tip_angle": None})
        else:
            raw_pointer.append({"pdf_x": cx, "pdf_y": cy, "tip_angle": tip_angle,
                                 "area_pt2": area_pt2, "nl": nl})

    print(f"  Raw candidates: {len(raw_standing)} standing, {len(raw_pointer)} pointer",
          file=sys.stderr)

    # --- Filter text-glyph false positives ---
    # Only applies when text is rendered as outlined vector paths (some Illustrator
    # exports).  If the text stream and the drawings don't overlap on circular
    # characters, the filter would only remove real cone circles that happen to sit
    # under numeric cone labels — so skip it in that case.
    text_bboxes = []
    if has_outlined_text(page, drawings):
        text_bboxes = get_text_bboxes(page)
        n_before = len(raw_standing) + len(raw_pointer)
        raw_standing = filter_text_glyphs(raw_standing, text_bboxes)
        raw_pointer  = filter_text_glyphs(raw_pointer,  text_bboxes)
        n_after = len(raw_standing) + len(raw_pointer)
        if n_before != n_after:
            print(f"  Text filter: removed {n_before - n_after} glyph false positives "
                  f"({len(text_bboxes)} text spans)", file=sys.stderr)
    else:
        print("  Text filter: skipped (text is not outlined in this PDF)", file=sys.stderr)

    # --- Consensus-size filter for pointer candidates ---
    # All cone pointer symbols on a given map are drawn at the same size.
    # Numbering arrows and other annotations that slipped under MAX_CONE_AREA tend
    # to be outliers in the area distribution.  Use the lower-half median to anchor
    # the reference and discard anything outside [0.25×, 4×] median.
    if len(raw_pointer) > 4:
        raw_ptr_areas = sorted(c["area_pt2"] for c in raw_pointer)
        ref = raw_ptr_areas[: max(1, len(raw_ptr_areas) // 2)]
        median_area = ref[len(ref) // 2]
        lo, hi = median_area * 0.25, median_area * 4.0
        before = len(raw_pointer)
        raw_pointer = [c for c in raw_pointer if lo <= c["area_pt2"] <= hi]
        removed = before - len(raw_pointer)
        if removed:
            print(f"  Consensus-size filter: removed {removed} pointer outliers "
                  f"(median {median_area:.1f} pt², range {lo:.1f}–{hi:.1f})",
                  file=sys.stderr)

    # --- Line-count dominance filter for pointer candidates ---
    # Real pointer cone symbols in Illustrator-exported PDFs are consistently drawn
    # with the same number of line segments (e.g. 8 for the arrow-base symbol, 3 for
    # a plain triangle).  Annotation arrowheads and numbering arrows use a different
    # segment count.  If one nl value accounts for ≥60% of candidates, discard shapes
    # with a count more than 2 below it (they're a different symbol type entirely).
    if len(raw_pointer) > 4:
        from collections import Counter
        nl_counts = Counter(c["nl"] for c in raw_pointer)
        dominant_nl, dominant_count = nl_counts.most_common(1)[0]
        if dominant_count / len(raw_pointer) >= 0.6 and dominant_nl >= 5:
            min_nl = dominant_nl - 2
            before = len(raw_pointer)
            raw_pointer = [c for c in raw_pointer if c["nl"] >= min_nl]
            removed = before - len(raw_pointer)
            if removed:
                print(f"  Line-count filter: removed {removed} pointers with nl < {min_nl} "
                      f"(dominant nl={dominant_nl}, {dominant_count}/{before})",
                      file=sys.stderr)

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
        "type":     "scale",
        "scale":    round(m_per_pt, 6),
        "ox":       round(-m_per_pt * cx_centroid, 4),
        "oy":       round( m_per_pt * cy_centroid, 4),
        "page_w_pt": round(page.rect.width,  3),
        "page_h_pt": round(page.rect.height, 3),
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
    if timing_start_cones or timing_end_cones:
        # Explicit cone numbers supplied in jobs file — look them up by text label.
        timing_start = []
        timing_end   = []
        if timing_start_cones:
            found = find_cones_by_label(
                page, drawings, timing_start_cones, m_per_pt, cx_centroid, cy_centroid)
            for c in found:
                c["type"] = "timing_start"
            timing_start = found
            print(f"  Timing start (labels {timing_start_cones}): {len(found)} cones found",
                  file=sys.stderr)
        if timing_end_cones:
            found = find_cones_by_label(
                page, drawings, timing_end_cones, m_per_pt, cx_centroid, cy_centroid)
            for c in found:
                c["type"] = "timing_end"
            timing_end = found
            print(f"  Timing end   (labels {timing_end_cones}): {len(found)} cones found",
                  file=sys.stderr)
    else:
        text_dets = detect_start_finish(page, drawings)
        print(f"  Text detections: {text_dets}", file=sys.stderr)
        standing, timing_start, timing_end = tag_timing_cones(
            standing, text_dets, m_per_pt, cx_centroid, cy_centroid,
        )

    # Extract gate endpoints (bar stroke endpoints → world space) for Blender markers.
    # These are stored separately from the timing cone list.
    def _gate_from_dets(dets, det_type):
        for d in dets:
            if d.get("type") == det_type and d.get("gate_a") and d.get("gate_b"):
                ax, ay = pdf_to_blender(d["gate_a"][0], d["gate_a"][1],
                                        cx_centroid, cy_centroid, m_per_pt)
                bx, by = pdf_to_blender(d["gate_b"][0], d["gate_b"][1],
                                        cx_centroid, cy_centroid, m_per_pt)
                return {"a": [ax, ay], "b": [bx, by]}
        return None

    if timing_start_cones or timing_end_cones:
        timing_start_gate = None
        timing_end_gate   = None
    else:
        timing_start_gate = _gate_from_dets(text_dets, "timing_start")
        timing_end_gate   = _gate_from_dets(text_dets, "timing_end")
        if invert_gates:
            if timing_start_gate:
                timing_start_gate = {"a": timing_start_gate["b"], "b": timing_start_gate["a"]}
            if timing_end_gate:
                timing_end_gate = {"a": timing_end_gate["b"], "b": timing_end_gate["a"]}
        if timing_start_gate:
            print(f"  Start gate: {timing_start_gate}", file=sys.stderr)
        if timing_end_gate:
            print(f"  Finish gate: {timing_end_gate}", file=sys.stderr)

    # Stage position: lowest-numbered section-1 cones near the start gate
    stage_cone_pos = None
    _start_det = next((d for d in text_dets if d["type"] == "timing_start"), None)
    _gate_pdf_x = _start_det["pdf_x"] if _start_det else None
    _gate_pdf_y = _start_det["pdf_y"] if _start_det else None
    _stage_pdf = _detect_stage_position(page, _gate_pdf_x, _gate_pdf_y)
    if _stage_pdf is not None:
        sx, sy = pdf_to_blender(_stage_pdf[0], _stage_pdf[1], cx_centroid, cy_centroid, m_per_pt)
        stage_cone_pos = [sx, sy]

    # --- Pointer orientation (tip_from_pdf angle, fallback toward nearest standing cone) ---
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
        "transform":          transform,
        "pointer_source":     "pdf",
        "n_standing":         len(standing),
        "n_pointer":          len(pointers),
        "n_timing_start":     len(timing_start),
        "n_timing_end":       len(timing_end),
        "n_gcp":              0,
        "bounds":             bounds,
        "standing":           standing,
        "pointers":           pointers,
        "timing_start":       timing_start,
        "timing_end":         timing_end,
        "timing_start_gate":  timing_start_gate,
        "timing_end_gate":    timing_end_gate,
        "stage_cone_pos":     stage_cone_pos,
        "gcp":                [],
    }

    # Strip internal-only fields before writing
    _INTERNAL_FIELDS = {"tip_from_pdf", "nearest_standing_idx", "nearest_standing_dist"}

    def strip_internal(cone):
        return {k: v for k, v in cone.items() if k not in _INTERNAL_FIELDS}

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

    if chalk_path:
        from detect_chalk_lines import extract_chalk_paths, render_chalk_mask
        chalk_paths = extract_chalk_paths(page)
        # 1 pt = 1 ft at 72 DPI; convert inches → pixels
        line_px = max(1, round(chalk_width_in / 12.0))
        chalk_img = render_chalk_mask(page, chalk_paths, line_width_px=line_px, dpi=72)
        chalk_img.save(chalk_path)
        print(f"  Chalk mask: {chalk_path} ({chalk_img.width}×{chalk_img.height} px, "
              f"{len(chalk_paths)} paths, line={chalk_width_in}\" → {line_px}px)",
              file=sys.stderr)

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

    # Draw gate bars and direction arrows
    # Precompute which perpendicular side of each gate has more cones (interior side)
    all_standing = result["standing"]

    # Reference points in pixel space for interior-side determination
    _b = result.get("bounds", {})
    _cent_px, _cent_py = bl_to_px(
        (_b.get("xmin", 0) + _b.get("xmax", 0)) / 2,
        (_b.get("ymin", 0) + _b.get("ymax", 0)) / 2,
    )
    _stage = result.get("stage_cone_pos")
    _stage_px, _stage_py = (bl_to_px(_stage[0], _stage[1]) if _stage else (_cent_px, _cent_py))

    def _interior_perp_px(ax, ay, bx, by, ref_px=None, ref_py=None):
        """Return the screen-space perpendicular pointing toward (ref_px, ref_py)."""
        if ref_px is None:
            ref_px, ref_py = _cent_px, _cent_py
        mx, my = (ax + bx) / 2, (ay + by) / 2
        bdx, bdy = bx - ax, by - ay
        blen = math.hypot(bdx, bdy) or 1.0
        px1, py1 = -bdy / blen, bdx / blen
        to_cx = ref_px - mx
        to_cy = ref_py - my
        return (px1, py1) if to_cx * px1 + to_cy * py1 > 0 else (-px1, -py1)

    def _draw_gate_arrow(gate, color, toward_interior, ref_px=None, ref_py=None):
        """Draw bar line + travel-direction arrow for a timing gate."""
        if not gate:
            return
        ax, ay = bl_to_px(gate["a"][0], gate["a"][1])
        bx, by = bl_to_px(gate["b"][0], gate["b"][1])
        draw.line([(ax, ay), (bx, by)], fill=color, width=max(3, int(4 * scale)))

        mx, my = (ax + bx) / 2, (ay + by) / 2
        ix, iy = _interior_perp_px(ax, ay, bx, by, ref_px, ref_py)
        dx, dy = (ix, iy) if toward_interior else (-ix, -iy)

        arrow_len = max(20, int(30 * scale))
        tip_x = mx + dx * arrow_len
        tip_y = my + dy * arrow_len
        draw.line([(mx, my), (tip_x, tip_y)], fill=color, width=max(2, int(3 * scale)))
        head = arrow_len * 0.35
        lx = tip_x - dx * head + dy * head * 0.5
        ly = tip_y - dy * head - dx * head * 0.5
        rx = tip_x - dx * head - dy * head * 0.5
        ry = tip_y - dy * head + dx * head * 0.5
        draw.polygon([(tip_x, tip_y), (lx, ly), (rx, ry)], fill=color)

    # Start arrow: away from stage (stage is behind the gate)
    _draw_gate_arrow(result.get("timing_start_gate"), (0, 220, 80),
                     toward_interior=False, ref_px=_stage_px, ref_py=_stage_py)
    _draw_gate_arrow(result.get("timing_end_gate"),   (220, 60, 60), toward_interior=False)

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
    p.add_argument("--snap-pointers", action="store_true", default=False,
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
        snap_pointers=args.snap_pointers,
        snap_radius_m=args.snap_radius,
    )
