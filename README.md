# assetto-corsa-autocross-blender-generator
Convert an overhead autocross map image or json export into a 1:1 scale Assetto Corsa track.


## Alpha.  
Only tested running in windows on power shell :vomit: Json format is a bit jank.  Image recognition is difficult and format specific.  

Should work with the output of https://ezmac.github.io/ax-mapper

Json may be reworked soon.

Needs testing on linux but I've had issues running kseditor on linux.

## Requirements

- Python: system Python with `pillow`, `scipy`, `numpy` installed (run `build_track.py` with that interpreter)
- Blender (auto-detected from standard install locations)
- Packages: `pillow`, `scipy`, `numpy`

---

## Templates:
This now supports building flat maps without a template, but results are better with them.


See template_requirements.md for detailed info.  To get started quickly, download the assets from this video's description. 
https://mega.nz/file/084lnAxb#TKQA9plb322QE7iX4t_HheZRzxGbp5H9-ozUkBv9IjU
goes in templates/.  Because uniformity, rename all files and folders to be rem_gymkhana instead of ac_gymkhana.
File provided by Route Eight Media in this video's description: https://www.youtube.com/watch?v=wjnjIFCT2wQ

I'd like to fix this so that I'm not sending people at mega.nz, but haven't found a good way yet.  Comment on issues if you know of one.


## Build from an image

```
python build_track.py --name <track_name> --image <map.png> [options]
```

The image should have orange standing cones and magenta pointer cones
(use the `_magenta_pointers.png` variant if available).

**Example:**

```
python build_track.py --name seneca_gp_21 --image source_images/Seneca_Grand_Prix_2021_magenta_pointers.png --fbx
```

**With GCP calibration** (required for accurate scale and position):

```
python build_track.py --name seneca_gp_21 --image source_images/map.png \
    --gcp-left-img   11371 4248  --gcp-left-blender   -37.185 105.608 \
    --gcp-right-img  19734 4246  --gcp-right-blender  154.726 109.324 \
    --gcp3-img       23730 13437 --gcp3-blender        246.058 -96.129 \
    --fbx
```

What happens:

1. `detect_cones.py` detects cones in the image and writes a JSON file to
   `generated/<track_name>_cones.json`
2. The selected template is copied to `generated/<track_name>/`
3. Blender runs headlessly to place cones and save the `.blend` file
4. If `--fbx` is given, an FBX is exported next to the blend file

---

## Build from JSON

```
python build_track.py --name <track_name> --json <cones.json> [options]
```

The JSON should contain world-space Blender coordinates — e.g. output from a prior
`--image` run, from `detect_cones.py` directly, or exported from
[ax-mapper](https://ezmac.github.io/ax-mapper).

**Example:**

```
python build_track.py --name flat_experiment --json flat_experiment.json --template rem_gymkhana
```

**With GCP calibration** (JSON was produced from an image using GCP arguments — coordinates
are already calibrated; pass the JSON path directly):

```
python build_track.py --name seneca_gp_21 \
    --json generated/seneca_gp_21/debug/seneca_gp_21.json \
    --template seneca_runway --no-flat --fbx
```

To generate a GCP-calibrated JSON from an image, run `detect_cones.py` separately first
(see [Build from an image](#build-from-an-image) for the `--gcp-*` flags).

What happens:

1. The selected template is copied to `generated/<track_name>/`
2. Blender runs headlessly to place cones and save the `.blend` file
3. If `--fbx` is given, an FBX is exported next to the blend file

---

## Build a flat map without a template

If you don't have a pre-built `.blend` template, `new_flat_project.py` procedurally
generates a flat road surface, grass, perimeter walls, and all required AC data files
from scratch:

```
python new_flat_project.py <track_name> [--width 120] [--length 80] [options]
```

**Example:**

```
python new_flat_project.py my_event_v2 --width 150 --length 100
```

What happens:

1. Creates `generated/<track_name>/` with all AC data files (`surfaces.ini`, `ui_track.json`, etc.)
2. Blender runs headlessly to build the scene and export an FBX
3. Open the resulting `.blend`, run `place_cones_flat.py` with your cone JSON, then export FBX and import to ksEditor

> **Note:** When using `build_track.py --no-template`, road dimensions are calculated
> automatically from your cone JSON bounds (with 20 m padding on each side), so
> `--width` / `--length` are not needed. Use `new_flat_project.py` directly only if
> you want to set dimensions manually.

**Options (`new_flat_project.py`):**

| Flag | Description |
|---|---|
| `<track_name>` | Track name (positional) |
| `--width M` | Road surface width in metres (default: 120) |
| `--length M` | Road surface length in metres (default: 80) |
| `--blender PATH` | Path to Blender executable (auto-detected if omitted) |
| `--cone-blend PATH` | `.blend` file containing cone asset (defaults to `templates/rem_gymkhana/blender/asset/Cone01.blend` if present) |

---

## Options (`build_track.py`)

| Flag | Description |
|---|---|
| `--name NAME` | Track name — used for output folder, blend file, and AC data folder |
| `--image PATH` | Source map image (runs cone detection first) |
| `--json PATH` | Pre-generated cone JSON (skips detection) |
| `--pdf PATH` | Course map PDF (runs PDF cone detection) |
| `--page N` | 1-indexed page number within the PDF (default: 1) |
| `--template NAME` | Template to copy from `templates/` (default: `rem_gymkhana`) |
| `--no-template` | Generate flat map geometry procedurally — road dimensions derived from cone JSON bounds |
| `--cone-blend PATH` | `.blend` file with cone asset for `--no-template` mode |
| `--blender PATH` | Path to `blender.exe` (auto-detected if omitted) |
| `--flat` | Force flat surface mode (Z=0 for all cones) |
| `--no-flat` | Force BVH raycast mode (snaps cones to road mesh elevation) |
| `--fbx` | Export FBX after cone placement |
| `--out-json PATH` | Where to save detected cone JSON (image/pdf mode only) |
| `--preview PATH` | Save annotated detection image (image/pdf mode only) |
| `--map PATH` | Save clean map PNG at 72 DPI (pdf mode only) |
| `--no-snap-pointers` | Disable pointer-to-standing-cone snapping (pdf mode only) |
| `--snap-radius M` | Max snap distance in metres (pdf mode only) |
| `--list-templates` | List available templates and exit |

---

## Templates

Templates are not currently included because the small template is 80MB.  This is a todo item.  You can get the base template I started with in the description of this youtube video: https://www.youtube.com/watch?v=wjnjifct2wq.  To work with these scripts, unpack it in templates/rem_gymkhana.  Check all files in the unpacked directory and rename "ac_gymkhana" to be "rem_gymkhana".  For now the naming of templates is strict.  That's another todo.


| Template | Surface mode | Notes |
|---|---|---|
| `rem_gymkhana` | flat | Flat pavement, no elevation |
| `seneca_runway` | raycast | Airport runway with real elevation data |

To list available templates:

```
python build_track.py --list-templates
```

---

## After Blender

1. Open the `.blend` in Blender and verify cone placement
2. **File → Export → FBX**, scale setting = **FBX All**
   (or pass `--fbx` to `build_track.py` to do this automatically)
3. Open the FBX in **ksEditor**
5. Export KN5 from ksEditor

---

## Re-importing cones interactively

To re-place cones in an already-open Blender scene without re-running the full pipeline,
run from the Blender Scripting tab:

```python
exec(open(r"<path-to-assetto-corsa-autocross-blender-generator>\place_cones_flat.py").read())   # flat scenes
exec(open(r"<path-to-assetto-corsa-autocross-blender-generator>\place_cones.py").read())         # elevation scenes
```

Edit `JSON_PATH` at the top of the script to point to your cone JSON before running.

---

## Scripts

| Script | Purpose |
|---|---|
| `build_track.py` | CLI entry point — full pipeline from image or JSON |
| `detect_cones.py` | Image → cone JSON |
| `blender_place_cones.py` | Blender headless cone placement (called by `build_track.py`) |
| `place_cones_flat.py` | Interactive flat-surface cone placement (Blender Scripting tab) |
| `place_cones.py` | Interactive elevation-aware cone placement (Blender Scripting tab) |
| `new_project.py` | Copy the `seneca_runway` template to a new project folder (rename only — no cone placement). Use this to start a new elevation-terrain track variant without running the full pipeline. |
| `new_flat_project.py` | Create a flat-map project from scratch with no pre-built template. Procedurally generates road, grass, walls, and all AC data files via Blender, then place cones separately with `place_cones_flat.py`. |
| `create_flat_template.py` | Blender script called by `new_flat_project.py` — not run directly |
