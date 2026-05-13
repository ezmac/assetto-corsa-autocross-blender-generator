"""
detect_chalk_lines.py — Extract chalk outline paths from a SoloNationals course map PDF.

Chalk outlines are the grey polylines that mark the boundaries of the chalked course
surface.  They appear as solid grey strokes roughly double the grid line width, drawn
as connected line-segment (and occasionally bezier-curve) paths across the course.
The exact stroke weight varies by year/designer; the detector uses a wide range.

Outputs a white-on-black mask PNG at 1 px = 1 pt (72 DPI), matching the _map.png
scale produced by detect_cones_pdf.py.

Usage:
    python detect_chalk_lines.py \\
        --pdf  "SoloNationals/.../2019 Nationals Courses.pdf" \\
        --page 3 \\
        --out  generated/solonats/2019_east_chalk.png \\
        [--chalk-width 5.0]   # physical line width in inches (default: 5.0)
        [--debug map.png]     # also render a red overlay on an existing map PNG
"""

import argparse
import math
import sys
from pathlib import Path

import fitz  # pymupdf
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Filter parameters
# ---------------------------------------------------------------------------

# Chalk lines vary in stroke weight between PDFs (1.25 pt in 2019, 1.80 pt in 2013).
# Accept any grey solid stroke in this range — wider than a grid line, narrower than
# a heavy section marker (~4 pt or ~6 pt).
CHALK_WIDTH_MIN  = 0.9    # pt — floor (grid lines are 0.5–0.75 pt)
CHALK_WIDTH_MAX  = 3.0    # pt — ceiling (section markers are 4+ pt)

# A chalk path must have at least this many combined line+curve items.
# Short chalked sections (2–3 cones) can have as few as 2 segments.
CHALK_MIN_SEGS   = 1

# Reject large sparse paths (spectator boxes, grid boundaries): paths with very
# few vertices but a large bounding-box diagonal are not chalk outlines.
CHALK_SPARSE_MAX_SEGS = 4    # if seg count <= this AND diagonal > threshold → reject
CHALK_SPARSE_MIN_DIAG = 500  # pt (~500 ft) — minimum diagonal to trigger rejection

CHALK_GREY_LO    = 0.20   # min grey channel (not black = not cone/symbol)
CHALK_GREY_HI    = 0.85   # max grey channel (not white = not background)
CHALK_GREY_TOL   = 0.12   # max per-channel deviation from grey (R≈G≈B)

PAGE_MARGIN_PT   = 20.0   # discard paths whose centroid is outside this inset

# Bezier tessellation resolution (segments per curve item)
BEZIER_STEPS     = 8


def is_chalk_grey(color):
    if color is None or len(color) < 3:
        return False
    r, g, b = color[0], color[1], color[2]
    return (
        abs(r - g) < CHALK_GREY_TOL
        and abs(g - b) < CHALK_GREY_TOL
        and CHALK_GREY_LO < r < CHALK_GREY_HI
    )


def _bezier(p1, p2, p3, p4, steps=BEZIER_STEPS):
    """Tessellate a cubic bezier into (steps+1) (x,y) points."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3*p1.x + 3*u**2*t*p2.x + 3*u*t**2*p3.x + t**3*p4.x
        y = u**3*p1.y + 3*u**2*t*p2.y + 3*u*t**2*p3.y + t**3*p4.y
        pts.append((x, y))
    return pts


def extract_chalk_paths(page):
    """
    Return a list of paths, where each path is a list of (x, y) point tuples
    in PDF page coordinates (pt, y-down, origin top-left).
    Bezier curve items are approximated with line segments.
    """
    drawings = page.get_drawings()
    pr = page.rect
    inner = fitz.Rect(
        pr.x0 + PAGE_MARGIN_PT, pr.y0 + PAGE_MARGIN_PT,
        pr.x1 - PAGE_MARGIN_PT, pr.y1 - PAGE_MARGIN_PT,
    )

    chalk_paths = []
    for d in drawings:
        w      = d.get("width") or 0
        color  = d.get("color")
        dashes = d.get("dashes") or ""
        fill   = d.get("fill")

        if not is_chalk_grey(color):
            continue
        if fill is not None:
            continue
        if dashes.strip() not in ("", "[] 0"):
            continue
        if not (CHALK_WIDTH_MIN <= w <= CHALK_WIDTH_MAX):
            continue

        nl = sum(1 for it in d["items"] if it[0] == "l")
        nc = sum(1 for it in d["items"] if it[0] == "c")
        if nl + nc < CHALK_MIN_SEGS:
            continue

        # Reject large sparse paths (spectator/grid boundary rectangles)
        r = d["rect"]
        bbox_diag = math.sqrt(r.width ** 2 + r.height ** 2)
        if nl + nc <= CHALK_SPARSE_MAX_SEGS and bbox_diag > CHALK_SPARSE_MIN_DIAG:
            continue

        # Reject wall tick marks: very narrow paths that are also long (>100pt).
        # Short single-segment chalk stubs (inter-cone gaps) have max dim < 100pt.
        if nl + nc <= 3 and min(r.width, r.height) < 5 and max(r.width, r.height) > 100:
            continue

        # Check centroid is inside page inner margin
        cx = (r.x0 + r.x1) / 2
        cy = (r.y0 + r.y1) / 2
        if not inner.contains(fitz.Point(cx, cy)):
            continue

        # Build polyline: lines → endpoints, beziers → tessellated points
        pts = []
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if not pts or abs(pts[-1][0] - p1.x) > 0.01 or abs(pts[-1][1] - p1.y) > 0.01:
                    pts.append((p1.x, p1.y))
                pts.append((p2.x, p2.y))
            elif it[0] == "c":
                p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
                curve = _bezier(p1, p2, p3, p4)
                if pts and (abs(pts[-1][0] - curve[0][0]) > 0.01 or abs(pts[-1][1] - curve[0][1]) > 0.01):
                    pts.append(curve[0])
                pts.extend(curve[1:])

        if len(pts) >= 2:
            chalk_paths.append(pts)

    return chalk_paths


def render_chalk_mask(page, chalk_paths, line_width_px=1, dpi=72, centered=False):
    """
    Render chalk paths as white lines on a black background.
    Canvas matches the PDF page at the given DPI (default 72 = 1px per pt).
    If centered=True, paths are assumed to be in centroid-offset space and rendered accordingly.
    Returns a PIL Image (mode 'L').
    """
    pr    = page.rect
    scale = dpi / 72.0

    if centered:
        # Paths are already centered on centroid; calculate canvas size from path bounds
        if not chalk_paths or not chalk_paths[0]:
            # Empty paths, create minimal image
            img = Image.new("L", (1, 1), color=0)
            return img
        all_pts = [pt for path in chalk_paths for pt in path]
        xs = [x for x, y in all_pts]
        ys = [y for x, y in all_pts]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        w_px = int(math.ceil((xmax - xmin) * scale)) + 2
        h_px = int(math.ceil((ymax - ymin) * scale)) + 2
        offset_x = -xmin * scale + 1
        offset_y = -ymin * scale + 1
    else:
        # Paths are in page coordinates
        w_px  = int(math.ceil(pr.width  * scale))
        h_px  = int(math.ceil(pr.height * scale))
        offset_x = offset_y = 0

    img  = Image.new("L", (w_px, h_px), color=0)
    draw = ImageDraw.Draw(img)

    for path in chalk_paths:
        if len(path) < 2:
            continue
        pixel_pts = [(x * scale + offset_x, y * scale + offset_y) for x, y in path]
        draw.line(pixel_pts, fill=255, width=line_width_px, joint="curve")

    return img


def render_debug_overlay_with_page(page, map_path, chalk_paths, line_width_px=2):
    """Draw chalk paths in red over the existing map PNG and return the composite."""
    base = Image.open(map_path).convert("RGB")
    pr   = page.rect
    sx   = base.width  / pr.width
    sy   = base.height / pr.height

    draw = ImageDraw.Draw(base)
    for path in chalk_paths:
        if len(path) < 2:
            continue
        pixel_pts = [(x * sx, y * sy) for x, y in path]
        draw.line(pixel_pts, fill=(255, 60, 60), width=max(2, line_width_px), joint="curve")
    return base


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--pdf",         required=True, help="Path to course map PDF")
    ap.add_argument("--page",        type=int, default=1,
                    help="1-indexed page number (default: 1)")
    ap.add_argument("--out",         required=True, help="Output PNG path for chalk mask")
    ap.add_argument("--chalk-width", type=float, default=5.0,
                    help="Physical chalk line width in inches (default: 5.0). "
                         "Converted to pixels at the output DPI (72 DPI = 1px/ft).")
    ap.add_argument("--dpi",         type=float, default=72,
                    help="Output resolution in DPI (default: 72 = 1px per pt)")
    ap.add_argument("--debug",       help="Path to map PNG; also writes a red overlay")
    args = ap.parse_args()

    pdf  = fitz.open(args.pdf)
    page = pdf[args.page - 1]

    chalk_paths = extract_chalk_paths(page)
    print(f"Found {len(chalk_paths)} chalk paths")
    for i, p in enumerate(chalk_paths):
        xs = [pt[0] for pt in p]
        ys = [pt[1] for pt in p]
        print(f"  Path {i}: {len(p)} pts  "
              f"bbox=({min(xs):.0f},{min(ys):.0f})-({max(xs):.0f},{max(ys):.0f})")

    # 72 DPI = 1px per ft (1pt = 1ft); convert inches → pixels
    line_px = max(1, round(args.chalk_width / 12.0 * args.dpi / 72.0))
    print(f"Chalk width: {args.chalk_width}\" → {line_px}px at {args.dpi} DPI")
    mask = render_chalk_mask(page, chalk_paths, line_width_px=line_px, dpi=args.dpi)

    out_path = Path(args.out)
    if not out_path.suffix:
        out_path = out_path.with_suffix(".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(str(out_path))
    print(f"Chalk mask: {out_path} ({mask.width}×{mask.height} px)")

    if args.debug:
        debug_img = render_debug_overlay_with_page(
            page, args.debug, chalk_paths, line_width_px=line_px
        )
        debug_path = out_path.with_name(out_path.stem + "_debug.png")
        debug_img.save(str(debug_path))
        print(f"Debug overlay: {debug_path}")


if __name__ == "__main__":
    main()
