#!/usr/bin/env python
"""
label_cones.py — Interactive cone labeler for the autocross cone detector.

Labels cone positions in the source map image so that an SVM classifier can
be trained to replace the hand-crafted classification rules.

Usage:
    python label_cones.py path/to/image.jpg
    python label_cones.py path/to/image.jpg --output labels.json
    python label_cones.py path/to/image.jpg --existing labels.json   # continue

Controls:
    Left-click      Select a cone position (yellow dot shows pending)
    S               Label last click as Standing cone  (blue square overlay)
    P               Label last click as Pointer cone   (red triangle overlay)
    Z  or  U        Undo the last labeled cone
    Q  or  Escape   Save and quit

Output (labels.json):
    {
      "image": "/abs/path/to/image.jpg",
      "labels": [
        {"x": 411, "y": 1003, "type": "s"},
        {"x": 438, "y": 1008, "type": "p"},
        ...
      ]
    }

Coordinates are in the original image pixel space (not the downsampled display).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Interactive labeler
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Interactive cone labeler — click cones, press S or P to label.'
    )
    parser.add_argument('image', help='Path to map image (JPEG, PNG, BMP …)')
    parser.add_argument('--output', '-o', default='labels.json',
                        help='Output labels JSON (default: labels.json)')
    parser.add_argument('--existing', '-e', default=None,
                        help='Load existing labels.json to continue editing')
    parser.add_argument('--zoom', type=int, default=25,
                        help='Zoom radius in original-image pixels (default: 25)')
    args = parser.parse_args()

    # ---- load image --------------------------------------------------------
    img_path = Path(args.image)
    if not img_path.exists():
        sys.exit(f"ERROR: image not found: {args.image}")

    img = Image.open(img_path).convert('RGB')
    arr = np.array(img)
    H, W = arr.shape[:2]
    print(f"Image: {W}x{H}  ({img_path.name})")

    # Downsample large images for display (matplotlib struggles with huge images)
    max_display = 1400
    if max(W, H) > max_display:
        disp_scale = max_display / max(W, H)
        disp_w = int(W * disp_scale)
        disp_h = int(H * disp_scale)
        disp_img = img.resize((disp_w, disp_h), Image.LANCZOS)
        disp_arr = np.array(disp_img)
        print(f"Displayed at {disp_w}x{disp_h} (scale {disp_scale:.3f})")
    else:
        disp_scale = 1.0
        disp_arr = arr

    # ---- load existing labels if asked ------------------------------------
    labels = []
    if args.existing and Path(args.existing).exists():
        with open(args.existing) as f:
            data = json.load(f)
        labels = data.get('labels', [])
        s_n = sum(1 for l in labels if l['type'] == 's')
        p_n = sum(1 for l in labels if l['type'] == 'p')
        print(f"Loaded {len(labels)} existing labels ({s_n} standing, {p_n} pointer) "
              f"from {args.existing}")

    # ---- matplotlib --------------------------------------------------------
    import matplotlib.pyplot as plt

    fig, (ax_main, ax_zoom) = plt.subplots(
        1, 2,
        figsize=(16, 9),
        gridspec_kw={'width_ratios': [3, 1]},
    )
    fig.patch.set_facecolor('#1e1e1e')

    ax_main.imshow(disp_arr)
    ax_main.set_axis_off()

    ax_zoom.set_facecolor('#111')
    ax_zoom.set_axis_off()

    # Overlay scatter plots
    sc_standing = ax_main.scatter(
        [], [], c='#4488ff', s=70, marker='s', zorder=5, label='Standing'
    )
    sc_pointer = ax_main.scatter(
        [], [], c='#ff4444', s=70, marker='^', zorder=5, label='Pointer'
    )
    sc_pending = ax_main.scatter(
        [], [], c='yellow', s=100, marker='o', zorder=6
    )
    ax_main.legend(
        handles=[sc_standing, sc_pointer],
        loc='upper right',
        facecolor='#333',
        labelcolor='white',
        fontsize=9,
    )

    # State
    pending = [None]   # pending_click = (display_x, display_y) or None

    def _title():
        s_n = sum(1 for l in labels if l['type'] == 's')
        p_n = sum(1 for l in labels if l['type'] == 'p')
        return (f"{img_path.name} — {len(labels)} labeled  "
                f"({s_n} standing · {p_n} pointer)\n"
                "Click cone → S (standing) or P (pointer)    Z=undo    Q=save & quit")

    def refresh():
        s_xs = [l['x'] * disp_scale for l in labels if l['type'] == 's']
        s_ys = [l['y'] * disp_scale for l in labels if l['type'] == 's']
        p_xs = [l['x'] * disp_scale for l in labels if l['type'] == 'p']
        p_ys = [l['y'] * disp_scale for l in labels if l['type'] == 'p']
        sc_standing.set_offsets(
            list(zip(s_xs, s_ys)) if s_xs else np.empty((0, 2))
        )
        sc_pointer.set_offsets(
            list(zip(p_xs, p_ys)) if p_xs else np.empty((0, 2))
        )
        fig.suptitle(_title(), color='white', fontsize=10)
        fig.canvas.draw_idle()

    def show_zoom(orig_x, orig_y):
        r = args.zoom
        x0 = max(0, orig_x - r)
        x1 = min(W, orig_x + r + 1)
        y0 = max(0, orig_y - r)
        y1 = min(H, orig_y + r + 1)
        patch = arr[y0:y1, x0:x1]
        ax_zoom.clear()
        ax_zoom.imshow(patch, interpolation='nearest')
        cx_p = orig_x - x0
        cy_p = orig_y - y0
        ax_zoom.axhline(cy_p, color='yellow', linewidth=0.8, alpha=0.8)
        ax_zoom.axvline(cx_p, color='yellow', linewidth=0.8, alpha=0.8)
        ax_zoom.set_title(
            f'({orig_x}, {orig_y})\nS = standing   P = pointer',
            color='white', fontsize=9
        )
        ax_zoom.set_facecolor('#111')
        ax_zoom.tick_params(colors='#888')
        fig.canvas.draw_idle()

    def on_click(event):
        if event.inaxes is not ax_main:
            return
        if event.button != 1:
            return
        dx, dy = event.xdata, event.ydata
        if dx is None or dy is None:
            return
        pending[0] = (dx, dy)
        sc_pending.set_offsets([[dx, dy]])
        orig_x = int(round(dx / disp_scale))
        orig_y = int(round(dy / disp_scale))
        orig_x = max(0, min(W - 1, orig_x))
        orig_y = max(0, min(H - 1, orig_y))
        show_zoom(orig_x, orig_y)

    def on_key(event):
        key = (event.key or '').lower()

        if key in ('s', 'p'):
            if pending[0] is None:
                print("  ↳ Click a cone first, then press S or P")
                return
            dx, dy = pending[0]
            orig_x = int(round(dx / disp_scale))
            orig_y = int(round(dy / disp_scale))
            orig_x = max(0, min(W - 1, orig_x))
            orig_y = max(0, min(H - 1, orig_y))
            ltype = key  # 's' or 'p'
            labels.append({'x': orig_x, 'y': orig_y, 'type': ltype})
            label_str = 'standing' if ltype == 's' else 'pointer'
            print(f"  + ({orig_x:5d}, {orig_y:5d})  {label_str:<9s}  "
                  f"[{len(labels)} total]")
            pending[0] = None
            sc_pending.set_offsets(np.empty((0, 2)))
            refresh()

        elif key in ('z', 'u'):
            if labels:
                removed = labels.pop()
                label_str = 'standing' if removed['type'] == 's' else 'pointer'
                print(f"  - ({removed['x']:5d}, {removed['y']:5d})  "
                      f"{label_str:<9s}  undone  [{len(labels)} remaining]")
            pending[0] = None
            sc_pending.set_offsets(np.empty((0, 2)))
            refresh()

        elif key in ('q', 'escape'):
            _save_and_exit()

    def _save_and_exit():
        out_path = args.output
        data = {
            'image': str(img_path.resolve()),
            'labels': labels,
        }
        with open(out_path, 'w') as f:
            json.dump(data, f, indent=2)
        s_n = sum(1 for l in labels if l['type'] == 's')
        p_n = sum(1 for l in labels if l['type'] == 'p')
        print(f"\nSaved {len(labels)} labels ({s_n} standing, {p_n} pointer) "
              f"→ {out_path}")
        plt.close('all')
        sys.exit(0)

    # Wire up events
    fig.canvas.mpl_connect('button_press_event', on_click)
    fig.canvas.mpl_connect('key_press_event', on_key)

    refresh()
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    plt.show()

    # If window closed without pressing Q
    _save_and_exit()


if __name__ == '__main__':
    main()
