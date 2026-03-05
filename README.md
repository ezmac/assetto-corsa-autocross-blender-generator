# assetto-corsa-autocross-blender-generator
Convert an overhead autocross map image or json export into a 1:1 scale Assetto Corsa track.


## Alpha.  Only tested running in windows on power shell :vomit: Json format is a bit jank.  Image recognition is difficult and format specific.  
Should work with the output of https://ezmac.github.io/ax-mapper

Json schema to be reworked soon.
Project to be renamed soon.  It no longer _needs_ ai to run properly.

Needs testing on linux but I've had issues running kseditor on linux.

## Requirements

- Python: system Python with `pillow`, `scipy`, `numpy` installed (run `build_track.py` with that interpreter)
- Blender (auto-detected from standard install locations)
- Packages: `pillow`, `scipy`, `numpy`

---

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

**With GCP calibration** (required for accurate scale and position): ``` python build_track.py --name seneca_gp_21 --image source_images/map.png \ --gcp-left-img   11371 4248  --gcp-left-blender   -37.185 105.608 \ --gcp-right-img  19734 4246  --gcp-right-blender  154.726 109.324 \ --gcp3-img       23730 13437 --gcp3-blender        246.058 -96.129 \
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

If you already have cone data (e.g. from a previous detection run or external tool):

```
python build_track.py --name <track_name> --json <cones.json> [options]
```

**Example:**

```
python build_track.py --name flat_experiment --json flat_experiment.json --template rem_gymkhana
```

Steps 2–4 above run; cone detection is skipped.

---

## Options

| Flag | Description |
|---|---|
| `--name NAME` | Track name — used for output folder, blend file, and AC data folder |
| `--image PATH` | Source map image (runs cone detection first) |
| `--json PATH` | Pre-generated cone JSON (skips detection) |
| `--template NAME` | Template to copy from `templates/` (default: `rem_gymkhana`) |
| `--blender PATH` | Path to `blender.exe` (auto-detected if omitted) |
| `--flat` | Force flat surface mode (Z=0 for all cones) |
| `--no-flat` | Force BVH raycast mode (snaps cones to road mesh elevation) |
| `--fbx` | Export FBX after cone placement |
| `--out-json PATH` | Where to save detected cone JSON (image mode only) |
| `--preview PATH` | Save annotated detection image (image mode only) |
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
4. For each material, set Shader: `ksPerPixel`, assign diffuse texture to `txDiffuse`
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
| `new_project.py` | Copy seneca_runway template to a new project folder |
