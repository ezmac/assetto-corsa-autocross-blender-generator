"""
build_track.py — Build an Assetto Corsa autocross track from an image, PDF, or JSON file.

Usage:
    python build_track.py --name <name> --json  <path.json>           [options]
    python build_track.py --name <name> --image <path.png>            [options]
    python build_track.py --name <name> --pdf   <path.pdf> --page N   [options]

Options:
    --name NAME         Track name — used for folder, blend file, and AC data folder (required)
    --json PATH         Pre-generated cone JSON (skip detection)
    --image PATH        Source map image (runs detect_cones.py first)
    --pdf PATH          Course map PDF (runs detect_cones_pdf.py; also extracts course outline)
    --page N            1-indexed page number within the PDF (default: 1)
    --template NAME     Template to copy from templates/ (default: rem_gymkhana)
    --no-template       Generate flat map geometry procedurally (no pre-built template needed)
                        Road dimensions are calculated from the cone JSON bounds automatically.
    --cone-blend PATH   .blend file with cone asset for --no-template mode
    --blender PATH      Path to blender.exe (auto-detected if omitted)
    --flat / --no-flat  Override surface mode (default depends on template)
    --fbx               Export FBX at the end (placed next to the blend file)
    --preview PATH      Save annotated detection image (image/pdf mode)
    --map PATH          Save clean map PNG at 72 DPI (pdf mode only)
    --out-json PATH     Where to save detected JSON (default: generated/<name>/debug/<name>.json)
    --snap-pointers        Enable pointer snapping (pdf mode only, off by default)
    --snap-radius M        Max snap distance in metres (pdf mode only)
    --invert-start-gate    Swap start gate endpoints (pdf mode only)
    --invert-finish-gate   Swap finish gate endpoints (pdf mode only)

GCP overrides (image mode only — passed through to detect_cones.py):
    --gcp-left-img   X Y    --gcp-left-blender   BX BY
    --gcp-right-img  X Y    --gcp-right-blender  BX BY
    --gcp3-img       X Y    --gcp3-blender       BX BY

Examples:
    python build_track.py --name mytrack --json mytrack_cones.json
    python build_track.py --name mytrack --image map.png --template rem_gymkhana --fbx
    python build_track.py --name 2018_west --pdf "Nationals Courses.pdf" --page 2 --fbx
    python build_track.py --name seneca_v7 --json cone_data_affine.json --template seneca_runway --no-flat
    python build_track.py --name my_event --json cones.json --no-template --fbx

Jobs mode (Solo Nationals batch):
    python build_track.py --jobs solonats_jobs.json [--only 2021_west 2013_east] [--fbx]
    Reads the same jobs file used by run_pdf_detection.py.  Tracks are named
    solonats_<job_name> and the pre-detected JSON from generated/solonats/ is used.
"""

import sys
import os
import platform
import argparse
import shutil
import re
import json
import glob
import subprocess
import math

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR   = os.path.join(SCRIPT_DIR, 'templates')
GENERATED_DIR   = os.path.join(SCRIPT_DIR, 'generated')
DETECT_SCRIPT   = os.path.join(SCRIPT_DIR, 'detect_cones.py')
DETECT_PDF_SCRIPT = os.path.join(SCRIPT_DIR, 'detect_cones_pdf.py')
PLACE_SCRIPT    = os.path.join(SCRIPT_DIR, 'blender_place_cones.py')
FLAT_TEMPLATE_SCRIPT = os.path.join(SCRIPT_DIR, 'create_flat_template.py')
SYSTEM_PYTHON   = sys.executable

sys.path.insert(0, SCRIPT_DIR)
import new_flat_project

DEFAULT_TEMPLATE = 'rem_gymkhana'

# Surface mode default per template name. Any unlisted template defaults to flat.
FLAT_DEFAULTS = {
    'rem_gymkhana':  True,
    'seneca_runway': False,
}


def find_blender():
    """Search for the Blender executable across platforms."""
    # Check PATH first — works on all platforms when Blender is installed normally
    blender = shutil.which('blender')
    if blender:
        return blender

    system = platform.system()
    if system == 'Windows':
        patterns = [
            r'C:\Program Files\Blender Foundation\Blender*\blender.exe',
            r'C:\Program Files\Blender Foundation\blender.exe',
            r'C:\Users\*\AppData\Roaming\Blender Foundation\Blender\*\blender.exe',
        ]
    elif system == 'Darwin':
        patterns = [
            '/Applications/Blender.app/Contents/MacOS/Blender',
            '/Applications/Blender*.app/Contents/MacOS/Blender',
            os.path.expanduser('~/Applications/Blender.app/Contents/MacOS/Blender'),
            os.path.expanduser('~/Applications/Blender*.app/Contents/MacOS/Blender'),
        ]
    else:  # Linux
        patterns = [
            '/snap/bin/blender',
            os.path.expanduser('~/snap/blender/current/usr/bin/blender'),
            '/var/lib/flatpak/exports/bin/org.blender.Blender',
            os.path.expanduser('~/.local/share/flatpak/exports/bin/org.blender.Blender'),
            '/usr/local/bin/blender',
            '/usr/bin/blender',
        ]

    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return sorted(matches)[-1]
    return None


def find_main_blend(blender_dir):
    """Find the main .blend in the blender folder, ignoring asset/ subdirs and .blend1 backups."""
    candidates = []
    for root, dirs, files in os.walk(blender_dir):
        dirs[:] = [d for d in dirs if d.lower() != 'asset']
        for f in files:
            if f.endswith('.blend') and not f.endswith('.blend1'):
                candidates.append(os.path.join(root, f))
    if not candidates:
        sys.exit(f"ERROR: No .blend file found under {blender_dir}")
    if len(candidates) == 1:
        return candidates[0]
    # Multiple candidates: prefer one in a 'project' subdirectory
    for c in candidates:
        if 'project' in os.path.dirname(c).lower():
            return c
    return candidates[0]


def setup_project(name, template_name):
    """Copy template to generated/<name>/, rename AC data folder and blend, update ui_track.json."""
    src_dir  = os.path.join(TEMPLATES_DIR, template_name)
    dest_dir = os.path.join(GENERATED_DIR, name)

    if not os.path.isdir(src_dir):
        sys.exit(f"ERROR: Template not found: {src_dir}")
    if os.path.exists(dest_dir):
        sys.exit(f"ERROR: Project already exists: {dest_dir}\n"
                 f"       Delete it first or choose a different name.")

    print(f"Copying template '{template_name}' -> {dest_dir}")
    shutil.copytree(src_dir, dest_dir)

    # ── Locate blender dir and AC data folder ─────────────────────────────────
    # Template has exactly two subdirs: blender/ and <template_name>/ (the AC data folder).
    blender_dir = None
    ac_src      = None
    for item in os.listdir(dest_dir):
        full = os.path.join(dest_dir, item)
        if not os.path.isdir(full):
            continue
        if item.lower() == 'blender':
            blender_dir = full
        else:
            ac_src = item   # the folder named after the template

    if not blender_dir:
        sys.exit("ERROR: No 'blender' folder found in template.")
    if not ac_src:
        sys.exit("ERROR: No AC data folder found in template (expected a non-blender subdir).")

    # ── Rename AC data folder -> <name> ──────────────────────────────────────
    ac_dst = os.path.join(dest_dir, name)
    os.rename(os.path.join(dest_dir, ac_src), ac_dst)
    print(f"AC folder:    {ac_src} -> {name}")

    # ── Update ui_track.json ──────────────────────────────────────────────────
    ui_path = os.path.join(ac_dst, 'ui', 'ui_track.json')
    if os.path.isfile(ui_path):
        with open(ui_path, 'r', encoding='utf-8') as f:
            content = f.read()
        human_name = name.replace('_', ' ').title()
        updated = re.sub(r'("name"\s*:\s*)"[^"]*"', rf'\1"{human_name}"', content)
        with open(ui_path, 'w', encoding='utf-8') as f:
            f.write(updated)
        print(f"ui_track.json: name -> \"{human_name}\"")
    else:
        print(f"WARNING: ui_track.json not found at {ui_path}")

    # ── Find and rename main blend file -> <name>.blend ───────────────────────
    blend_src = find_main_blend(blender_dir)
    blend_dst = os.path.join(os.path.dirname(blend_src), name + '.blend')
    blend1    = blend_src.replace('.blend', '.blend1')
    if os.path.exists(blend1):
        os.remove(blend1)
    os.rename(blend_src, blend_dst)
    print(f"Blend:        {os.path.relpath(blend_dst, dest_dir)}")

    # ── Rename .kn5 and .fbx file(s) matching the template name ──────────────
    for root, _dirs, files in os.walk(dest_dir):
        for f in files:
            if f == f"{ac_src}.kn5":
                old_path = os.path.join(root, f)
                new_path = os.path.join(root, f"{name}.kn5")
                os.rename(old_path, new_path)
                print(f"KN5:          {os.path.relpath(old_path, dest_dir)} -> {name}.kn5")
            elif f.startswith(ac_src) and f.lower().endswith('.fbx'):
                suffix   = f[len(ac_src):]               # e.g. ".fbx" or "_TREES.fbx"
                old_path = os.path.join(root, f)
                new_path = os.path.join(root, f"{name}{suffix}")
                os.rename(old_path, new_path)
                print(f"FBX:          {os.path.relpath(old_path, dest_dir)} -> {name}{suffix}")

    # ── Patch .fbx.ini: rename file and update internal FBX name references ──
    # ksEditor stores shader assignments keyed by FBX filename. The template's
    # .fbx.ini may reference an old FBX name that differs from both the template
    # folder name and the new track name, so we detect it from the file content.
    import re as _re
    for root, _dirs, files in os.walk(dest_dir):
        for f in files:
            if not f.lower().endswith('.fbx.ini'):
                continue
            ini_path = os.path.join(root, f)
            text = open(ini_path).read()
            # Extract the old FBX basename from the first [model_FBX: <name>_...] line
            m = _re.search(r'\[model_FBX: ([^\]_]+\.fbx)', text, _re.IGNORECASE)
            old_fbx_name = m.group(1) if m else None
            new_fbx_name = f"{name}.fbx"
            if old_fbx_name:
                text = _re.sub(_re.escape(old_fbx_name), new_fbx_name, text, flags=_re.IGNORECASE)
            new_ini_path = os.path.join(root, f"{name}.fbx.ini")
            with open(new_ini_path, 'w') as fh:
                fh.write(text)
            if new_ini_path != ini_path:
                os.remove(ini_path)
            print(f"FBX.INI:      {os.path.relpath(ini_path, dest_dir)} -> {name}.fbx.ini"
                  + (f" (patched {old_fbx_name!r} -> {new_fbx_name!r})" if old_fbx_name else ""))

    return dest_dir, blend_dst


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
        b['xmin'] = min(b['xmin'], stage[0])
        b['xmax'] = max(b['xmax'], stage[0])
        b['ymin'] = min(b['ymin'], stage[1])
        b['ymax'] = max(b['ymax'], stage[1])

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


def detect_cones(image_path, out_json, extra_args):
    """Run detect_cones.py on an image to produce a cone JSON."""
    cmd = [
        SYSTEM_PYTHON, DETECT_SCRIPT,
        '--image', image_path,
        '--out',   out_json,
    ] + extra_args
    print(f"\n-- detect_cones.py {'-'*50}")
    print(' '.join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ERROR: detect_cones.py failed (exit {result.returncode})")
    print(f"Cone JSON: {out_json}")


def detect_cones_pdf(pdf_path, page, out_json, preview_path, map_path, extra_args):
    """Run detect_cones_pdf.py on a PDF page to produce cone JSON."""
    cmd = [
        SYSTEM_PYTHON, DETECT_PDF_SCRIPT,
        '--pdf',    pdf_path,
        '--page',   str(page),
        '--out',    out_json,
    ]
    if preview_path:
        cmd += ['--preview', preview_path]
    if map_path:
        cmd += ['--map', map_path]
    cmd += extra_args
    print(f"\n-- detect_cones_pdf.py {'-'*46}")
    print(' '.join(f'"{a}"' if ' ' in a else a for a in cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ERROR: detect_cones_pdf.py failed (exit {result.returncode})")
    print(f"Cone JSON: {out_json}")


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


def _run_detection(args, out_json, debug_dir):
    """Run cone detection from image or PDF into out_json. Returns out_json."""
    os.makedirs(debug_dir, exist_ok=True)
    if args.image:
        extra = []
        preview = args.preview or os.path.join(debug_dir, f'{args.name}_preview.png')
        extra += ['--preview', preview]
        for flag, vals in [
            ('--gcp-left-img',      args.gcp_left_img),
            ('--gcp-left-blender',  args.gcp_left_blender),
            ('--gcp-right-img',     args.gcp_right_img),
            ('--gcp-right-blender', args.gcp_right_blender),
            ('--gcp3-img',          args.gcp3_img),
            ('--gcp3-blender',      args.gcp3_blender),
        ]:
            if vals:
                extra += [flag] + [str(v) for v in vals]
        detect_cones(args.image, out_json, extra)
    elif args.pdf:
        extra = []
        if args.snap_pointers:
            extra.append('--snap-pointers')
        if args.snap_radius is not None:
            extra += ['--snap-radius', str(args.snap_radius)]
        if args.invert_start_gate:
            extra.append('--invert-start-gate')
        if args.invert_finish_gate:
            extra.append('--invert-finish-gate')
        preview  = args.preview or os.path.join(debug_dir, f'{args.name}_preview.png')
        map_path = args.map     or os.path.join(debug_dir, f'{args.name}_map.png')
        detect_cones_pdf(
            pdf_path=args.pdf,
            page=args.page,
            out_json=out_json,
            preview_path=preview,
            map_path=map_path,
            extra_args=extra,
        )
    return out_json


def run_create_flat_template(blender_exe, name, dest_dir, width, length, cone_blend):
    """Invoke Blender headlessly to build a flat scene from scratch."""
    cmd = [
        blender_exe,
        '--background',
        '--python', FLAT_TEMPLATE_SCRIPT,
        '--',
        '--name',   name,
        '--width',  str(width),
        '--length', str(length),
        '--output', dest_dir,
    ]
    if cone_blend:
        cmd += ['--cone-blend', cone_blend]
    print(f"\n-- create_flat_template {'-'*44}")
    print(' '.join(f'"{a}"' if ' ' in a else a for a in cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ERROR: create_flat_template.py failed (exit {result.returncode})")
    return os.path.join(dest_dir, 'blender', f'{name}.blend')


def run_blender(blender_exe, blend_path, json_path, flat, fbx_path):
    """Invoke Blender headlessly to place cones and export FBX."""
    cmd = [
        blender_exe,
        '--background', blend_path,
        '--python', PLACE_SCRIPT,
        '--',
        '--json', json_path,
    ]
    if flat:
        cmd.append('--flat')
    if fbx_path:
        cmd += ['--fbx', fbx_path]

    print(f"\n-- Blender {'-'*57}")
    print(' '.join(f'"{a}"' if ' ' in a else a for a in cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ERROR: Blender exited with code {result.returncode}")


SOLONATS_DETECT_DIR = os.path.join('generated', 'solonats')


def _convert_editor_json(editor_data, auto_data):
    """Convert editor project.json format {cones, scale, siteW, siteH} to pipeline format."""
    t = auto_data.get('transform', {})
    scale = t.get('scale', editor_data.get('scale', 0.3048))
    ox    = t.get('ox', 0.0)
    oy    = t.get('oy', 0.0)

    def to_blender(x, y):
        return round(x * scale + ox, 3), round(-y * scale + oy, 3)

    standing, pointers, timing_start, timing_end, stage_cones = [], [], [], [], []
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
            stage_cones.append([bx, by])

    if stage_cones:
        stage_cone_pos = [
            round(sum(c[0] for c in stage_cones) / len(stage_cones), 3),
            round(sum(c[1] for c in stage_cones) / len(stage_cones), 3),
        ]
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
        'gcp':              auto_data.get('gcp', []),
        'n_standing':       len(standing),
        'n_pointer':        len(pointers),
        'n_timing_start':   len(timing_start),
        'n_timing_end':     len(timing_end),
    }


def _merge_corrected_json(corrected_path, auto_path, out_path):
    """Write out_path = corrected JSON converted to pipeline format, with transform from auto_path."""
    with open(corrected_path) as f:
        data = json.load(f)
    auto = {}
    if os.path.isfile(auto_path):
        with open(auto_path) as f:
            auto = json.load(f)

    if 'cones' in data and 'standing' not in data:
        data = _convert_editor_json(data, auto)
    else:
        t = data.setdefault('transform', {})
        for key in ('page_w_pt', 'page_h_pt'):
            if key not in t and key in auto.get('transform', {}):
                t[key] = auto['transform'][key]

    with open(out_path, 'w') as f:
        json.dump(data, f, indent=2)


def _regen_chalk_png(job, chalk_path):
    """Regenerate chalk PNG from the original PDF using full-page rendering."""
    pdf_path = job.get('pdf')
    page_num = job.get('page', 1)
    if not pdf_path or not os.path.isfile(pdf_path):
        return False
    try:
        import fitz
        from detect_chalk_lines import extract_chalk_paths, render_chalk_mask
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        chalk_paths = extract_chalk_paths(page)
        line_px = max(1, round(job.get('chalk_width', 5.0) / 12.0))
        chalk_img = render_chalk_mask(page, chalk_paths, line_width_px=line_px, dpi=72, centered=False)
        chalk_img.save(chalk_path)
        print(f"  Chalk PNG: {chalk_path} ({chalk_img.width}×{chalk_img.height} px)")
        return True
    except Exception as e:
        print(f"  WARNING: could not regenerate chalk PNG: {e}", file=sys.stderr)
        return False


def _patch_solonats_metadata(dest_dir, track_name, designers, map_image_path):
    """Append designer credit and ezmac note to description; set ui/preview.png to the map image."""
    ui_path = os.path.join(dest_dir, track_name, 'ui', 'ui_track.json')
    if not os.path.isfile(ui_path):
        print(f"WARNING: ui_track.json not found at {ui_path}")
        return

    with open(ui_path, 'r', encoding='utf-8') as f:
        content = f.read()

    def _append(m):
        existing = m.group(1)
        suffix = (f' Designed by {designers}.' if designers else '') + ' Generated by ezmac.'
        return f'"description": "{existing}{suffix}"'

    updated = re.sub(r'"description"\s*:\s*"([^"]*)"', _append, content)
    with open(ui_path, 'w', encoding='utf-8') as f:
        f.write(updated)
    print(f"ui_track.json: appended credit (designers={designers!r})")

    if map_image_path and os.path.isfile(map_image_path):
        preview_path = os.path.join(dest_dir, track_name, 'ui', 'preview.png')
        shutil.copy2(map_image_path, preview_path)
        print(f"ui/preview.png <- {os.path.basename(map_image_path)}")
    else:
        print(f"WARNING: map image not found, preview.png not updated ({map_image_path})")


def run_jobs(jobs_path, only_names, fbx, blender_exe, generated_dir=None):
    """Process a jobs JSON file, building each solonats track."""
    with open(jobs_path) as f:
        jobs = json.load(f)

    only = set(only_names) if only_names else None
    errors = 0
    for job in jobs:
        name = job.get('name', '?')
        if job.get('skip'):
            print(f"  Skipping {name} (skip=true)")
            continue
        if only and name not in only:
            continue

        track_name = f'solonats_{name}'
        job_dir    = os.path.join(SOLONATS_DETECT_DIR, f'solonats_{name}')
        json_path  = os.path.join(job_dir, f'{name}.json')

        # Check for corrected JSON override
        if job.get('corrected_json'):
            corrected_path = job['corrected_json']
            if not os.path.isfile(corrected_path):
                print(f"ERROR: {name}: corrected_json specified but file not found: {corrected_path}", file=sys.stderr)
                errors += 1
                continue
            print(f"  Using corrected JSON: {corrected_path}")
            # Merge page_w_pt/page_h_pt from auto-generated JSON if absent in corrected JSON,
            # then regenerate chalk PNG so it aligns with the full-page UV mapping in Blender.
            merged_path = os.path.join(job_dir, f'{name}_merged.json')
            _merge_corrected_json(corrected_path, json_path, merged_path)
            chalk_path = os.path.splitext(merged_path)[0] + '_chalk.png'
            if not _regen_chalk_png(job, chalk_path):
                # Fall back to copying any existing chalk PNG
                existing = os.path.join(job_dir, f'{name}_chalk.png')
                if os.path.isfile(existing):
                    import shutil
                    shutil.copy2(existing, chalk_path)
            json_path = merged_path

        if not os.path.isfile(json_path):
            print(f"ERROR: {name}: detected JSON not found at {json_path} — "
                  f"run run_pdf_detection.py first", file=sys.stderr)
            errors += 1
            continue

        # Regenerate chalk PNG if it doesn't exist or is stale, ensuring full-page alignment
        chalk_path = os.path.splitext(json_path)[0] + '_chalk.png'
        json_mtime = os.path.getmtime(json_path)
        chalk_missing = not os.path.isfile(chalk_path)
        chalk_stale = os.path.isfile(chalk_path) and os.path.getmtime(chalk_path) < json_mtime
        if chalk_missing or chalk_stale:
            _regen_chalk_png(job, chalk_path)

        print(f"\n{'='*65}")
        print(f"  Track: {track_name}")
        print(f"  JSON:  {json_path}")
        print(f"{'='*65}\n")

        # Build sys.argv for the single-track main() to parse
        argv = [
            sys.argv[0],
            '--name',        track_name,
            '--json',        json_path,
            '--no-template',
        ]
        if fbx:
            argv.append('--fbx')
        if blender_exe:
            argv += ['--blender', blender_exe]
        if generated_dir:
            argv += ['--generated-dir', generated_dir]

        old_argv = sys.argv
        sys.argv = argv
        failed = False
        try:
            main()
        except SystemExit as e:
            if e.code and e.code != 0:
                print(f"ERROR building {track_name}: exit {e.code}", file=sys.stderr)
                errors += 1
                failed = True
        finally:
            sys.argv = old_argv

        if not failed:
            # Extract designers from the PDF parent folder (e.g. "2009 Chris Cox, Michael Feldpusch")
            pdf_path = job.get('pdf', '')
            designers = ''
            if pdf_path:
                folder = os.path.basename(os.path.dirname(pdf_path))
                designers = re.sub(r'^\d{4}\s+', '', folder)
            map_image = os.path.join(job_dir, f'{name}_map.png')
            track_dest = os.path.join(generated_dir or GENERATED_DIR, track_name)
            _patch_solonats_metadata(track_dest, track_name, designers, map_image)

    if errors:
        sys.exit(f"{errors} job(s) failed")


def main():
    # ── Quick exits before full arg parsing ───────────────────────────────────
    if '--list-templates' in sys.argv:
        print("Available templates:")
        for t in sorted(os.listdir(TEMPLATES_DIR)):
            if os.path.isdir(os.path.join(TEMPLATES_DIR, t)):
                default = 'flat' if FLAT_DEFAULTS.get(t, True) else 'raycast'
                print(f"  {t:30s}  ({default})")
        sys.exit(0)

    # ── Jobs mode (Solo Nationals batch) ─────────────────────────────────────
    if '--jobs' in sys.argv:
        jp = argparse.ArgumentParser(add_help=False)
        jp.add_argument('--jobs',          required=True)
        jp.add_argument('--only',          nargs='*', default=None)
        jp.add_argument('--fbx',           action='store_true', default=False)
        jp.add_argument('--blender',       default=None)
        jp.add_argument('--generated-dir', default=GENERATED_DIR, dest='generated_dir')
        ja, _ = jp.parse_known_args()
        run_jobs(ja.jobs, ja.only, ja.fbx, ja.blender, ja.generated_dir)
        return

    # ── Parse args ────────────────────────────────────────────────────────────
    p = argparse.ArgumentParser(
        description='Build an AC autocross track from an image or JSON file.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--name',         required=True, help='Track name')
    p.add_argument('--generated-dir', default=GENERATED_DIR, dest='generated_dir',
                   help='Root output directory (default: generated/)')
    p.add_argument('--json',      default=None,  help='Cone data JSON path')
    p.add_argument('--image',     default=None,  help='Source map image')
    p.add_argument('--pdf',       default=None,  help='Course map PDF')
    p.add_argument('--page',      type=int, default=1,
                   help='1-indexed page number within the PDF (default: 1)')
    p.add_argument('--template',      default=DEFAULT_TEMPLATE,
                   help=f'Template name from templates/ folder (default: {DEFAULT_TEMPLATE})')
    p.add_argument('--no-template',   action='store_true',
                   help='Generate flat map geometry procedurally (no pre-built template needed); '
                        'road dimensions are derived from the cone JSON bounds automatically')
    p.add_argument('--cone-blend', default=None,
                   help='Path to .blend with cone asset (--no-template only)')
    p.add_argument('--list-templates', action='store_true',  # handled pre-parse above
                   help='List available templates and exit')
    p.add_argument('--blender',   default=None,  help='Path to Blender executable')
    p.add_argument('--flat',      action='store_true',  default=None,
                   help='Force flat surface mode')
    p.add_argument('--no-flat',   action='store_true',
                   help='Force BVH raycast mode')
    p.add_argument('--fbx',       action='store_true',
                   help='Export FBX after cone placement')
    p.add_argument('--out-json',  default=None,
                   help='Where to save detected JSON (default: generated/<name>_cones.json)')
    p.add_argument('--preview',   default=None,
                   help='Save annotated detection image (image/pdf mode)')
    p.add_argument('--map',       default=None,
                   help='Save clean map PNG at 72 DPI (pdf mode only)')
    p.add_argument('--snap-pointers', action='store_true', default=False,
                   help='Enable pointer snapping (pdf mode only, off by default)')
    p.add_argument('--snap-radius', type=float, default=None, metavar='M',
                   help='Max snap distance in metres (pdf mode only)')
    p.add_argument('--invert-start-gate', action='store_true', default=False,
                   help='Swap start gate endpoints (pdf mode only)')
    p.add_argument('--invert-finish-gate', action='store_true', default=False,
                   help='Swap finish gate endpoints (pdf mode only)')
    # GCP overrides (image mode only)
    p.add_argument('--gcp-left-img',      nargs=2, type=float, default=None)
    p.add_argument('--gcp-left-blender',  nargs=2, type=float, default=None)
    p.add_argument('--gcp-right-img',     nargs=2, type=float, default=None)
    p.add_argument('--gcp-right-blender', nargs=2, type=float, default=None)
    p.add_argument('--gcp3-img',          nargs=2, type=float, default=None)
    p.add_argument('--gcp3-blender',      nargs=2, type=float, default=None)

    args = p.parse_args()

    # Validate input source
    sources = [s for s in (args.json, args.image, args.pdf) if s]
    if not sources:
        p.error("Provide one of --json, --image, or --pdf")
    if len(sources) > 1:
        p.error("Provide only one of --json, --image, or --pdf")
    if args.no_template and args.template != DEFAULT_TEMPLATE:
        p.error("--no-template and --template are mutually exclusive")

    template_name = args.template

    # Determine flat mode
    if args.no_flat:
        flat = False
    elif args.flat:
        flat = True
    elif args.no_template:
        flat = True  # procedurally generated surface is always flat
    else:
        flat = FLAT_DEFAULTS.get(template_name, True)

    # Find Blender
    blender_exe = args.blender or find_blender()
    if not blender_exe or not os.path.isfile(blender_exe):
        sys.exit("ERROR: Blender not found. Use --blender <path> to specify it.")
    print(f"Blender: {blender_exe}")

    dest_dir = os.path.join(args.generated_dir, args.name)

    print(f"\n{'='*65}")
    print(f"  Track:    {args.name}")
    if args.no_template:
        print(f"  Template: (none — procedural flat, dimensions from JSON)")
    else:
        print(f"  Template: {template_name}  (flat={flat})")
    print(f"  Output:   {dest_dir}")
    print(f"{'='*65}\n")

    # ── Step 1: Set up project ────────────────────────────────────────────────
    dest_dir  = os.path.join(args.generated_dir, args.name)
    debug_dir = os.path.join(dest_dir, 'debug')
    out_json  = args.out_json or os.path.join(debug_dir, f'{args.name}.json')
    json_path = args.json

    if args.json and os.path.isdir(dest_dir):
        # Re-run mode: project already exists, just find the blend file
        blend_path = find_main_blend(os.path.join(dest_dir, 'blender'))
        print(f"Reusing existing project: {dest_dir}")
    elif args.no_template:
        # Need JSON bounds before sizing the scene — detect first if image/pdf
        if not args.json:
            json_path = _run_detection(args, out_json, debug_dir)
        width, length = get_dims_from_json(json_path)
        print(f"  Flat dimensions: {width}m x {length}m")
        cone_blend = args.cone_blend or (
            new_flat_project.DEFAULT_CONE_BLEND
            if os.path.isfile(new_flat_project.DEFAULT_CONE_BLEND) else None
        )
        new_flat_project.create_project(args.name, width, length, dest_dir=dest_dir)
        os.makedirs(debug_dir, exist_ok=True)
        blend_path = run_create_flat_template(
            blender_exe, args.name, dest_dir, width, length, cone_blend
        )
    else:
        dest_dir, blend_path = setup_project(args.name, template_name)
        os.makedirs(debug_dir, exist_ok=True)

    # ── Step 2: Detect cones from image or PDF (if not already done above) ───
    if not args.no_template and (args.image or args.pdf):
        json_path = _run_detection(args, out_json, debug_dir)

    json_path = os.path.abspath(json_path)
    if not os.path.isfile(json_path):
        sys.exit(f"ERROR: JSON not found: {json_path}")

    # Regenerate chalk PNG from PDF source to ensure full-page alignment.
    # This is needed because chalk was previously rendered with tight crop (centered=True).
    chalk_path = os.path.splitext(json_path)[0] + '_chalk.png'
    import re
    name_match = re.search(r'solonats_(\d{4}[_a-z]*)', json_path)
    if name_match:
        name_base = name_match.group(1)
        if os.path.isfile('solonats_jobs.json'):
            with open('solonats_jobs.json') as f:
                jobs_data = json.load(f)
            for job in jobs_data:
                if job.get('name') == name_base:
                    if job.get('pdf'):
                        _regen_chalk_png(job, chalk_path)
                    break

    update_track_info(dest_dir, args.name, json_path)

    # ── Step 3: Place cones via Blender ──────────────────────────────────────
    # For --no-template builds, always export FBX: create_flat_template.py exports
    # one before placement, but it lacks cone placement and chalk UVs.
    fbx_path = None
    if args.fbx or args.no_template:
        fbx_path = os.path.join(dest_dir, 'blender', f'{args.name}.fbx')

    run_blender(blender_exe, blend_path, json_path, flat, fbx_path)

    # ── Patch .fbx.ini ROAD txDiffuse to chalk DDS (flat/no-template only) ───
    if args.no_template:
        _chalk_base   = os.path.splitext(os.path.basename(json_path))[0]
        _chalk_dds    = _chalk_base + '_chalk.dds'
        _fbx_ini_path = os.path.join(dest_dir, 'blender', f'{args.name}.fbx.ini')
        if os.path.isfile(_fbx_ini_path):
            _ini_text = open(_fbx_ini_path).read()
            # Replace txDiffuse for the ROAD material only (it's between [MATERIAL_0] and [MATERIAL_1])
            _road_block_end = _ini_text.find('[MATERIAL_1]')
            if _road_block_end == -1:
                _road_block_end = len(_ini_text)
            _road_block = _ini_text[:_road_block_end]
            _rest       = _ini_text[_road_block_end:]
            _road_block = re.sub(r'(RES_0_TEXTURE=).*', rf'\g<1>{_chalk_dds}', _road_block)
            with open(_fbx_ini_path, 'w') as _f:
                _f.write(_road_block + _rest)
            print(f"FBX.INI: patched ROAD txDiffuse → {_chalk_dds}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Done!  {args.name}")
    print(f"  Blend: {blend_path}")
    if fbx_path and os.path.isfile(fbx_path):
        print(f"  FBX:   {fbx_path}")
    if args.image or args.pdf:
        print(f"  Debug: {debug_dir}/")
        for f in sorted(os.listdir(debug_dir)):
            print(f"         {f}")
    print(f"{'='*65}")


if __name__ == '__main__':
    main()
