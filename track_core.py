"""
track_core.py — Pure-Python track-building logic.

No subprocess calls, no Blender dependency. Safe to import in tests and tools.
"""

import json
import math
import os
import re


def convert_editor_json(editor_data, auto_data):
    """Convert editor project.json format {cones, scale, siteW, siteH} to pipeline format."""
    t = auto_data.get('transform', {})
    scale = t.get('scale', editor_data.get('scale', 0.3048))
    ox    = t.get('ox', 0.0)
    oy    = t.get('oy', 0.0)

    def to_blender(x, y):
        return round(x * scale + ox, 3), round(-y * scale + oy, 3)

    standing, pointers, timing_start, timing_end, stage_cones, gcp_raw = [], [], [], [], [], []
    for cone in editor_data.get('cones', []):
        if cone.get('noExport'):
            continue
        ct = cone.get('coneType', 'standing')
        bx, by = to_blender(cone['x'], cone['y'])
        entry = {'bx': bx, 'by': by, 'type': ct, 'size': 1}
        if ct == 'standing':
            standing.append(entry)
        elif ct == 'pointer':
            # Editor rotation is CCW in screen (y-down) space; negate to get Blender facing_deg.
            entry['facing_deg'] = round(math.degrees(-cone.get('rotation', 0)), 1)
            pointers.append(entry)
        elif ct == 'timing_start':
            timing_start.append(entry)
        elif ct == 'timing_end':
            timing_end.append(entry)
        elif ct == 'car_start':
            facing = round(math.degrees(-cone.get('rotation', 0)), 1)
            stage_cones.append({'bx': bx, 'by': by, 'facing_deg': facing})
        elif ct == 'gcp':
            gcp_raw.append({'bx': bx, 'by': by})

    # Deduplicate near-identical GCPs (within 2 m) then cap at 3 (TOP_LEFT/TOP_RIGHT/BOTTOM_RIGHT).
    MIN_GCP_DIST_SQ = 4.0
    gcp_entries = []
    for g in gcp_raw:
        if any((g['bx'] - e['bx'])**2 + (g['by'] - e['by'])**2 < MIN_GCP_DIST_SQ
               for e in gcp_entries):
            print(f"  GCP dedup: dropped near-duplicate ({g['bx']}, {g['by']})")
            continue
        gcp_entries.append(g)
    if len(gcp_entries) > 3:
        print(f"  GCP: {len(gcp_entries)} unique GCPs found; using first 3")
        gcp_entries = gcp_entries[:3]

    if stage_cones:
        stage_cone_pos = {
            'bx': round(sum(c['bx'] for c in stage_cones) / len(stage_cones), 3),
            'by': round(sum(c['by'] for c in stage_cones) / len(stage_cones), 3),
            'facing_deg': round(sum(c['facing_deg'] for c in stage_cones) / len(stage_cones), 1),
        }
    else:
        stage_cone_pos = auto_data.get('stage_cone_pos')

    all_pts = standing + pointers + timing_start + timing_end
    if all_pts:
        bounds = {
            'xmin': min(c['bx'] for c in all_pts),
            'xmax': max(c['bx'] for c in all_pts),
            'ymin': min(c['by'] for c in all_pts),
            'ymax': max(c['by'] for c in all_pts),
        }
    else:
        bounds = auto_data.get('bounds')

    # Fill page dimensions into transform from auto JSON if absent
    for key in ('page_w_pt', 'page_h_pt'):
        if key not in t and key in auto_data.get('transform', {}):
            t[key] = auto_data['transform'][key]

    return {
        'transform':        t,
        'bounds':           bounds,
        'standing':         standing,
        'pointers':         pointers,
        'timing_start':     timing_start,
        'timing_end':       timing_end,
        'timing_start_gate': auto_data.get('timing_start_gate'),
        'timing_end_gate':   auto_data.get('timing_end_gate'),
        'stage_cone_pos':   stage_cone_pos,
        'gcp':              gcp_entries or auto_data.get('gcp', []),
        'n_standing':       len(standing),
        'n_pointer':        len(pointers),
        'n_timing_start':   len(timing_start),
        'n_timing_end':     len(timing_end),
    }


def get_dims_from_json(json_path, padding=20.0):
    """Return (width, length) in metres from cone bounds in JSON, with padding on each side.

    If the JSON transform includes page_w_pt / page_h_pt (solonats flat maps), the full
    page dimensions are used and padding is ignored — the road covers the whole site.
    """
    with open(json_path) as f:
        data = json.load(f)
    t = data.get('transform', {})
    if t.get('page_w_pt') and t.get('page_h_pt'):
        scale = t.get('scale', 0.3048)
        width  = round(t['page_w_pt']  * scale, 1)
        length = round(t['page_h_pt'] * scale, 1)
        return width, length
    b = data.get('bounds')
    if not b:
        all_pts = (data.get('standing', []) + data.get('pointers', [])
                   + data.get('timing_start', []) + data.get('timing_end', []))
        if not all_pts:
            return 120.0, 80.0
        b = {
            'xmin': min(c['bx'] for c in all_pts),
            'xmax': max(c['bx'] for c in all_pts),
            'ymin': min(c['by'] for c in all_pts),
            'ymax': max(c['by'] for c in all_pts),
        }
    width  = round((b['xmax'] - b['xmin']) + padding * 2, 1)
    length = round((b['ymax'] - b['ymin']) + padding * 2, 1)
    return width, length


def update_track_info(dest_dir, name, json_path):
    """Write cone count and lot size into ui_track.json description."""
    with open(json_path) as f:
        data = json.load(f)

    standing = data.get('standing', [])
    pointers = data.get('pointers', [])

    if 'bounds' in data:
        b = dict(data['bounds'])
    else:
        all_pts = standing + pointers + data.get('timing_start', []) + data.get('timing_end', [])
        b = {
            'xmin': min(c['bx'] for c in all_pts),
            'xmax': max(c['bx'] for c in all_pts),
            'ymin': min(c['by'] for c in all_pts),
            'ymax': max(c['by'] for c in all_pts),
        }

    stage = data.get('stage_cone_pos')
    if stage:
        sx = stage['bx'] if isinstance(stage, dict) else stage[0]
        sy = stage['by'] if isinstance(stage, dict) else stage[1]
        b['xmin'] = min(b['xmin'], sx)
        b['xmax'] = max(b['xmax'], sx)
        b['ymin'] = min(b['ymin'], sy)
        b['ymax'] = max(b['ymax'], sy)

    lot_w = b['xmax'] - b['xmin']
    lot_h = b['ymax'] - b['ymin']
    desc = (f"{len(standing)} standing + {len(pointers)} pointer cones. "
            f"Lot: {lot_w:.0f}m x {lot_h:.0f}m.")

    ui_path = os.path.join(dest_dir, name, 'ui', 'ui_track.json')
    if not os.path.isfile(ui_path):
        print(f"WARNING: ui_track.json not found at {ui_path}")
        return

    with open(ui_path, 'r', encoding='utf-8') as f:
        content = f.read()
    updated = re.sub(r'("description"\s*:\s*)"[^"]*"', rf'\1"{desc}"', content)
    with open(ui_path, 'w', encoding='utf-8') as f:
        f.write(updated)
    print(f"ui_track.json: description -> \"{desc}\"")


def merge_corrected_json(corrected_path, auto_path, out_path):
    """Write out_path = corrected JSON converted to pipeline format, with transform from auto_path."""
    with open(corrected_path) as f:
        data = json.load(f)
    auto = {}
    if os.path.isfile(auto_path):
        with open(auto_path) as f:
            auto = json.load(f)

    if 'cones' in data and 'standing' not in data:
        data = convert_editor_json(data, auto)
    else:
        t = data.setdefault('transform', {})
        for key in ('page_w_pt', 'page_h_pt'):
            if key not in t and key in auto.get('transform', {}):
                t[key] = auto['transform'][key]

    with open(out_path, 'w') as f:
        json.dump(data, f, indent=2)
