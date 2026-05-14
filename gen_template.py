#!/usr/bin/env python3
"""Generate a 1px=1ft runway outline template PNG for ax-mapper.

Uses:
  - road_boundary.json: boundary edge loops from 1ROAD1 and 1ROAD2 meshes
  - curve_data2.json: evaluated bezier curves for the infield loop

Rotates -1.1093° to straighten the runway top edge.
"""

import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

METERS_PER_FOOT = 0.3048
PX_PER_M = 1.0 / METERS_PER_FOOT  # 3.28084 px/m

ANGLE_DEG = -1.1093
_a = np.radians(ANGLE_DEG)
_cos, _sin = np.cos(_a), np.sin(_a)

def rot(pts):
    """Rotate Nx2 array by ANGLE_DEG."""
    x, y = pts[:, 0], pts[:, 1]
    return np.column_stack([_cos * x - _sin * y, _sin * x + _cos * y])


# Contest area crop in unrotated Blender metres
# x: include hammerhead and right extension up to just past GCP3 (x=246)
# y: full vertical extent of contest area
CROP_M_X = (-70.0, 290.0)
CROP_M_Y = (-200.0, 120.0)

# Convert to pixel space
CROP_X = (CROP_M_X[0] * PX_PER_M, CROP_M_X[1] * PX_PER_M)
CROP_Y = (CROP_M_Y[0] * PX_PER_M, CROP_M_Y[1] * PX_PER_M)


def main():
    with open('road_boundary.json') as f:
        road_data = json.load(f)
    with open('curve_data2.json') as f:
        curve_data = json.load(f)

    # Collect all polylines to draw
    polylines = []

    # Road mesh boundary loops
    for mesh_name in ['1ROAD1', '1ROAD2']:
        for loop in road_data.get(mesh_name, []):
            pts = np.array(loop, dtype=float)
            polylines.append(('boundary', rot(pts) * PX_PER_M))

    # Infield curve
    infield_pts = np.array(curve_data['infieldcurve'], dtype=float)
    polylines.append(('infield', rot(infield_pts) * PX_PER_M))

    w_px = int(round(CROP_X[1] - CROP_X[0]))
    h_px = int(round(CROP_Y[1] - CROP_Y[0]))
    print(f"Canvas: {w_px} × {h_px} px  ({w_px}ft × {h_px}ft)")
    print(f"  ≈ {w_px * METERS_PER_FOOT:.1f}m × {h_px * METERS_PER_FOOT:.1f}m")

    dpi = 96
    fig, ax = plt.subplots(figsize=(w_px / dpi, h_px / dpi), dpi=dpi)
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    lw = 2.0

    for kind, loop in polylines:
        xs = np.append(loop[:, 0], loop[0, 0])
        ys = np.append(loop[:, 1], loop[0, 1])
        ax.plot(xs, ys, 'k-', linewidth=lw, solid_capstyle='round',
                solid_joinstyle='round')

    ax.set_xlim(CROP_X)
    ax.set_ylim(CROP_Y)
    ax.set_aspect('equal')
    ax.axis('off')
    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

    out_path = '/home/tad/code/ax-mapping-demo/seneca_template_v2.png'
    fig.savefig(out_path, dpi=dpi, bbox_inches='tight', pad_inches=0,
                facecolor='white')
    plt.close(fig)
    print(f"Saved: {out_path}")


if __name__ == '__main__':
    main()
