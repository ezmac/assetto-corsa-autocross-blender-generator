"""
new_flat_project.py — Create a new flat-map AC autocross track project from scratch.

No pre-built .blend template required.  All geometry is procedurally generated
by create_flat_template.py running inside Blender headlessly.

Usage:
    python new_flat_project.py <track_name> [--width 120] [--length 80]

Example:
    python new_flat_project.py test_flat_01
    python new_flat_project.py my_event_v2 --width 150 --length 100

What it does:
    1. Creates generated/<track_name>/ directory structure
    2. Writes AC data files: surfaces.ini, map.ini, ext_config.ini, ui_track.json,
       placeholder preview.png / outline.png, empty .kn5
    3. Invokes Blender headlessly to run create_flat_template.py, which builds
       the Blender scene and exports the FBX

Next steps after running:
    - Open generated/<name>/blender/<name>.blend in Blender
    - Run place_cones_flat.py with your cone JSON
    - Export FBX from Blender (File > Export > FBX; scale = FBX All)
    - Import FBX into ksEditor, assign shaders, export KN5
    - Copy <name>/ folder to Assetto Corsa content/tracks/
"""

import sys
import os
import glob
import platform
import shutil
import struct
import zlib
import argparse
import subprocess

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
GENERATED_DIR = os.path.join(SCRIPT_DIR, 'generated')
BLENDER_SCRIPT    = os.path.join(SCRIPT_DIR, 'create_flat_template.py')
DEFAULT_CONE_BLEND = os.path.join(SCRIPT_DIR, 'templates', 'rem_gymkhana',
                                  'blender', 'asset', 'Cone01.blend')


# ── Blender discovery ─────────────────────────────────────────────────────────

def find_blender():
    blender = shutil.which('blender')
    if blender:
        return blender

    system = platform.system()
    if system == 'Windows':
        patterns = [
            r'C:\Program Files\Blender Foundation\Blender*\blender.exe',
            r'C:\Program Files\Blender Foundation\blender.exe',
        ]
    elif system == 'Darwin':
        patterns = [
            '/Applications/Blender.app/Contents/MacOS/Blender',
            '/Applications/Blender*.app/Contents/MacOS/Blender',
            os.path.expanduser('~/Applications/Blender.app/Contents/MacOS/Blender'),
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


# ── Minimal PNG writer (no external dependencies) ────────────────────────────

def _png_chunk(tag, data):
    crc = zlib.crc32(tag + data) & 0xffffffff
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)


def write_blank_png(path, width=512, height=512, rgb=(200, 200, 200)):
    """Write a solid-colour PNG without any external libraries."""
    r, g, b = rgb
    sig = b'\x89PNG\r\n\x1a\n'
    # IHDR: width, height, bit_depth=8, color_type=2 (RGB), compression=0, filter=0, interlace=0
    ihdr = _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    # Each row: 1 filter byte (0=None) + RGB pixels
    row  = bytes([0]) + bytes([r, g, b]) * width
    idat = _png_chunk(b'IDAT', zlib.compress(row * height, 9))
    iend = _png_chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(sig + ihdr + idat + iend)


# ── AC data file writers ──────────────────────────────────────────────────────

SURFACES_INI = """\
[SURFACE_0]
KEY=ROAD
FRICTION=0.98
DAMPING=0
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0
IS_VALID_TRACK=1
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0.0
VIBRATION_LENGTH=0.0

[SURFACE_1]
KEY=Grass
FRICTION=0.65
DAMPING=0.1
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0
IS_VALID_TRACK=0
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0.5
VIBRATION_LENGTH=0.0
"""

EXT_CONFIG_INI = """\
[INCLUDE]
INCLUDE=common/conditions.ini

[LIGHTING]
LIT_MULT=1.0
SPECULAR_MULT=1.0
CAR_LIGHTS_LIT_MULT=1.0

[GRASS_FX]
GRASS_MATERIALS=Grass
OCCLUDING_MATERIALS=Asphalt
MASK_BLUR=1
SHAPE_SIZE=1.0
SHAPE_CUT=0
SHAPE_TIDY=1.0
SHAPE_WIDTH=1.0

MASK_MAIN_THRESHOLD=0
MASK_RED_THRESHOLD=0.02
MASK_MIN_LUMINANCE=-0.5
MASK_MAX_LUMINANCE=0.5

[RAIN_FX]
PUDDLES_MATERIALS=Asphalt
"""


def write_map_ini(path, width, length):
    half_w = width / 2
    half_l = length / 2
    content = (
        "[PARAMETERS]\n"
        f"WIDTH={int(width)}\n"
        f"HEIGHT={int(length)}\n"
        f"X_OFFSET={half_w:.6f}\n"
        f"Z_OFFSET={half_l:.6f}\n"
        "MARGIN=10\n"
        "SCALE_FACTOR=1\n"
        "DRAWING_SIZE=10\n"
    )
    with open(path, 'w') as f:
        f.write(content)


def write_ui_track_json(path, track_name):
    human_name = track_name.replace('_', ' ').title()
    content = (
        '{\n'
        f'  "name": "{human_name}",\n'
        '  "description": "Flat autocross track",\n'
        '  "tags": [\n'
        '    "autocross"\n'
        '  ],\n'
        '  "geotags": [\n'
        '  ],\n'
        '  "country": "Any Country",\n'
        '  "pitboxes": "1",\n'
        '  "year": 2024,\n'
        '  "author": "",\n'
        '  "version": "1.0"\n'
        '}\n'
    )
    with open(path, 'w') as f:
        f.write(content)


# ── Project setup ─────────────────────────────────────────────────────────────

def create_project(name, width, length):
    dest_dir = os.path.join(GENERATED_DIR, name)
    if os.path.exists(dest_dir):
        print(f"ERROR: Project already exists: {dest_dir}")
        sys.exit(1)

    ac_dir  = os.path.join(dest_dir, name)
    data_dir = os.path.join(ac_dir, 'data')
    ext_dir  = os.path.join(ac_dir, 'extension')
    ui_dir   = os.path.join(ac_dir, 'ui')

    for d in (data_dir, ext_dir, ui_dir):
        os.makedirs(d, exist_ok=True)

    print(f"Created directory structure: {dest_dir}")

    # surfaces.ini
    with open(os.path.join(data_dir, 'surfaces.ini'), 'w') as f:
        f.write(SURFACES_INI)
    print("  data/surfaces.ini")

    # map.ini
    write_map_ini(os.path.join(data_dir, 'map.ini'), width, length)
    print("  data/map.ini")

    # ext_config.ini
    with open(os.path.join(ext_dir, 'ext_config.ini'), 'w') as f:
        f.write(EXT_CONFIG_INI)
    print("  extension/ext_config.ini")

    # ui_track.json
    write_ui_track_json(os.path.join(ui_dir, 'ui_track.json'), name)
    print("  ui/ui_track.json")

    # Placeholder PNG images (512×512 grey)
    write_blank_png(os.path.join(ui_dir, 'preview.png'), rgb=(128, 128, 128))
    write_blank_png(os.path.join(ui_dir, 'outline.png'), rgb=(255, 255, 255))
    print("  ui/preview.png  ui/outline.png")

    # Empty .kn5 placeholder (overwritten after ksEditor export)
    with open(os.path.join(ac_dir, f'{name}.kn5'), 'wb') as f:
        pass
    print(f"  {name}.kn5  (empty placeholder)")

    return dest_dir


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='Create a new flat AC autocross track project')
    p.add_argument('name',           help='Track name (used as folder and file names)')
    p.add_argument('--width',  type=float, default=120.0, help='Road width in metres (default 120)')
    p.add_argument('--length', type=float, default=80.0,  help='Road length in metres (default 80)')
    p.add_argument('--blender',    default=None, help='Path to Blender executable')
    p.add_argument('--cone-blend', default=None,
                   help='Path to .blend containing cone asset '
                        f'(default: {DEFAULT_CONE_BLEND} if it exists)')
    args = p.parse_args()

    name       = args.name
    width      = args.width
    length     = args.length
    cone_blend = args.cone_blend or (DEFAULT_CONE_BLEND if os.path.isfile(DEFAULT_CONE_BLEND) else None)

    print(f"\n{'='*60}")
    print(f"  New flat project: {name}")
    print(f"  Road: {width}m x {length}m")
    if cone_blend:
        print(f"  Cone: {os.path.basename(cone_blend)}")
    print(f"  Output: {os.path.join(GENERATED_DIR, name)}")
    print(f"{'='*60}\n")

    # ── Step 1: Create directory structure and AC data files ──────────────────
    dest_dir = create_project(name, width, length)

    # ── Step 2: Find Blender ──────────────────────────────────────────────────
    blender_exe = args.blender or find_blender()
    if not blender_exe or not os.path.isfile(blender_exe):
        print("\nERROR: Blender not found.")
        print("  Install Blender or pass --blender <path>")
        sys.exit(1)
    print(f"\nBlender: {blender_exe}")

    # ── Step 3: Run Blender headlessly to build the scene ─────────────────────
    cmd = [
        blender_exe,
        '--background',
        '--python', BLENDER_SCRIPT,
        '--',
        '--name',   name,
        '--width',  str(width),
        '--length', str(length),
        '--output', dest_dir,
    ]
    if cone_blend:
        cmd += ['--cone-blend', cone_blend]
    print(f"\n-- Blender {'-'*50}")
    print(' '.join(f'"{a}"' if ' ' in a else a for a in cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nERROR: Blender exited with code {result.returncode}")
        sys.exit(1)

    # ── Done ──────────────────────────────────────────────────────────────────
    blend_path = os.path.join(dest_dir, 'blender', f'{name}.blend')
    fbx_path   = os.path.join(dest_dir, 'blender', f'{name}.fbx')

    print(f"\n{'='*60}")
    print(f"  Done!  {name}")
    print(f"  Blend: {blend_path}")
    print(f"  FBX:   {fbx_path}")
    print(f"\nNext steps:")
    print(f"  1. Open {blend_path} in Blender")
    print(f"  2. Run place_cones_flat.py with your cone JSON")
    print(f"  3. Export FBX: File > Export > FBX (Apply Scalings = FBX All)")
    print(f"  4. Import FBX into ksEditor, assign shaders, export KN5")
    print(f"  5. Copy {os.path.join(dest_dir, name)}/ to AC content/tracks/")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
