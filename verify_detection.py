"""
verify_detection.py — Compare a detection JSON against a manually corrected ground truth.

Usage:
  python verify_detection.py <ground_truth.json> <detected.json> [--thresh 1.5]

Handles the coordinate-system difference between GT files (ox=0, oy=0 origin) and
detected files (centroid-relative origin) by using the transform field in each JSON.

Reports per-type: GT count, detected count, matched, missed, spurious, recall, precision.
"""

import argparse
import json
import math
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def world_origin(data):
    """Return (ox, oy) world-space origin from the transform block."""
    t = data.get("transform", {})
    return t.get("ox", 0.0), t.get("oy", 0.0)


def to_world(cone, ox, oy):
    """Shift bx/by from local (centroid-relative) space to world space."""
    return cone["bx"] - ox, cone["by"] - oy


def match(refs, ref_ox, ref_oy, dets, det_ox, det_oy, thresh):
    """
    Match ref cones to detected cones in world space.
    Returns (matched, missed, spurious).
    """
    used = set()
    matched = missed = 0
    for r in refs:
        rx, ry = to_world(r, ref_ox, ref_oy)
        best_d, best_i = 1e9, -1
        for i, d in enumerate(dets):
            dx, dy = to_world(d, det_ox, det_oy)
            dist = math.hypot(rx - dx, ry - dy)
            if dist < best_d:
                best_d, best_i = dist, i
        if best_d <= thresh and best_i not in used:
            matched += 1
            used.add(best_i)
        else:
            missed += 1
    spurious = len(dets) - len(used)
    return matched, missed, spurious


def report(label, refs, dets, ref_ox, ref_oy, det_ox, det_oy, thresh):
    if not refs and not dets:
        return
    matched, missed, spurious = match(refs, ref_ox, ref_oy, dets, det_ox, det_oy, thresh)
    recall    = matched / len(refs) * 100 if refs else 0.0
    precision = matched / len(dets) * 100 if dets else 0.0
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0
    print(
        f"{label:<10}  GT={len(refs):3d}  det={len(dets):3d}  "
        f"matched={matched:3d}  missed={missed:3d}  spurious={spurious:3d}  "
        f"recall={recall:5.1f}%  precision={precision:5.1f}%  F1={f1:5.1f}%"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ground_truth", help="Manually corrected JSON")
    ap.add_argument("detected",     help="Detection output JSON")
    ap.add_argument("--thresh", type=float, default=1.5,
                    help="Match distance threshold in metres (default: 1.5)")
    args = ap.parse_args()

    gt  = load(args.ground_truth)
    det = load(args.detected)

    gt_ox,  gt_oy  = world_origin(gt)
    det_ox, det_oy = world_origin(det)

    print(f"Ground truth:  {args.ground_truth}")
    print(f"Detected:      {args.detected}")
    print(f"Match thresh:  {args.thresh} m")
    print(f"GT origin:     ox={gt_ox:.3f}  oy={gt_oy:.3f}")
    print(f"Det origin:    ox={det_ox:.3f}  oy={det_oy:.3f}")
    print()

    report("Standing",     gt.get("standing",     []), det.get("standing",     []),
           gt_ox, gt_oy, det_ox, det_oy, args.thresh)
    report("Pointer",      gt.get("pointers",     []), det.get("pointers",     []),
           gt_ox, gt_oy, det_ox, det_oy, args.thresh)
    report("timing_start", gt.get("timing_start", []), det.get("timing_start", []),
           gt_ox, gt_oy, det_ox, det_oy, args.thresh)
    report("timing_end",   gt.get("timing_end",   []), det.get("timing_end",   []),
           gt_ox, gt_oy, det_ox, det_oy, args.thresh)


if __name__ == "__main__":
    main()
