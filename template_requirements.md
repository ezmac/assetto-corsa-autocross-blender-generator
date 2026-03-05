# Template Requirements

A template is a complete, ready-to-copy project. The pipeline copies it to
`generated/<name>/`, renames files to match the new track name, and then runs
Blender headlessly to place cones.

---

## File Tree

```
<template_name>/
├── blender/
│   ├── <template_name>.blend          # Main Blender project (required)
│   ├── <template_name>.fbx            # Last exported FBX (renamed on copy)
│   ├── <template_name>_TREES.fbx      # Optional scenery-only FBX variant
│   ├── texture/                       # Textures for Blender viewport preview
│   │   └── *.dds / *.png
│   └── *.npz                          # Elevation/terrain data (raycast templates only)
│
└── <template_name>/                   # AC track data folder (renamed on copy)
    ├── data/
    │   ├── surfaces.ini               # Physics surface definitions (required)
    │   └── map.ini                    # Track map display parameters
    ├── extension/
    │   └── ext_config.ini             # Lighting, weather, material overrides
    ├── ui/
    │   ├── ui_track.json              # Track metadata shown in AC menu (required)
    │   ├── preview.png                # Track preview image
    │   ├── outline.png                # Track outline for AC UI
    │   └── outline_cropped.png
    ├── map.png                        # In-game map image
    └── <template_name>.kn5            # Compiled AC track (renamed on copy)
```

### What gets renamed on `new_project.py` / `build_track.py`

| File | Renamed to |
|---|---|
| `blender/<template_name>.blend` | `blender/<name>.blend` |
| `blender/<template_name>.fbx` | `blender/<name>.fbx` |
| `blender/<template_name>_TREES.fbx` | `blender/<name>_TREES.fbx` |
| `<template_name>/` (folder) | `<name>/` |
| `<template_name>/<template_name>.kn5` | `<name>/<name>.kn5` |
| `<name>/ui/ui_track.json` `"name"` field | Human-readable track name |

---

## Required Files

| File | Purpose | Notes |
|---|---|---|
| `blender/<name>.blend` | Blender scene | Must contain all objects listed below |
| `blender/texture/` | Viewport textures | Paths in blend must be `//texture\<file>` (relative) |
| `<name>/data/surfaces.ini` | AC physics surfaces | Must define KEY entries matching object name prefixes |
| `<name>/ui/ui_track.json` | Track metadata | Must have `"name"` and `"description"` fields |
| `<name>/<name>.kn5` | Compiled track | Placeholder KN5; replaced after ksEditor export |

---

## Blender Scene Requirements

### Road and terrain meshes

These are the driveable and collidable surfaces. Object names control AC physics
via the prefix system — the number after the prefix is the surface index in
`surfaces.ini`.

| Object name | Surface type | Notes |
|---|---|---|
| `1ROAD0`, `1ROAD1`, `1ROAD2` | Asphalt (ROAD) | Driveable; used for Z-raycasting |
| `1GRASS0`, `1GRASS1` | Grass (GRASS) | Off-road; used as Z-raycast fallback |
| `1WALL0` | Collision wall | Invisible barrier around the lot |
| `Terrain` | Background plane | Flat ground at Z=0 (or Z=-0.5); not a physics surface |

For flat templates, the pipeline moves all four objects to the course center
and scales their mesh vertices if the course is larger than the current mesh.
All four must be present; any missing ones are silently skipped.

### Cone template object

| Object name | Required | Notes |
|---|---|---|
| `AC_POBJECT_MovableCone` | Yes | Mesh template; every placed cone copies this mesh via `.data.copy()` |

**Cone dimensions:** 11" base (0.2794 m) × 17" tall (0.4318 m).
The pipeline derives `POINTER_TILT` and `POINTER_Z_OFFSET` from the actual mesh
vertices at runtime, so rescaling the template cone is safe — re-run placement
afterwards.

The cone mesh must be wide enough at bumper height for AC physics to detect
standing cones reliably. The base radius must be ≥ ~5.5" (0.1397 m).

### AC spawn and timing markers

These objects must already exist in the scene. The placement script moves them
to the correct position relative to the detected start/finish gates.

| Object name | Type | Purpose |
|---|---|---|
| `AC_PIT_0` | Empty or Mesh | Pit spawn point |
| `AC_START_0` | Empty or Mesh | Race start spawn |
| `AC_HOTLAP_START_0` | Empty or Mesh | Hotlap start spawn |
| `AC_TIME_0_L`, `AC_TIME_0_R` | Empty | Timing start gate (left / right) |
| `AC_TIME_1_L`, `AC_TIME_1_R` | Empty | Timing finish gate (left / right) |

**Spawn marker rotation:** `(-90°, 0°, 0°)` in Blender Euler XYZ. Zero rotation
causes the car to spawn facing the sky. The placement script sets this
automatically.

**Spawn marker Z:** 1.5 m above the road surface.

**All AC markers must have at least one material slot.** Objects with no
material slot get assigned `FBX_MATERIAL` by ksEditor, which has no `txDiffuse`
— KN5 export fails. The placement script assigns a `Null` placeholder material
to any mesh marker with empty slots.

### GCP reference objects (elevation/raycast templates only)

Used to calibrate the image-to-Blender coordinate transform. Present in the
seneca_runway template; not needed for flat templates.

| Object name | Blender (X, Y) | Notes |
|---|---|---|
| `AC_POBJECT_GCP_P_TOP_LEFT` | (-37.185, 105.608) | |
| `AC_POBJECT_GCP_P_TOP_RIGHT` | (154.726, 109.324) | |
| `AC_POBJECT_GCP_P_BOTTOM_RIGHT` | (246.058, -96.129) | East bump-out, ~45° |

All GCP objects: `hide_render=True`, Null material, reference-only (must not
appear in-game).

### Scenery objects

These are repositioned by the placement script to surround the course bounds.

| Name pattern | Count | Notes |
|---|---|---|
| `StudiumLight` (prefix match) | 4 | Placed at corners; Z-rotation aimed at course center |
| `KSTREE_GROUP_A` (prefix match) | Any | Distributed evenly around perimeter rectangle |

---

## Materials

### Every object must have at least one material slot

Objects with no material slot cause ksEditor to assign `FBX_MATERIAL`, which
has no diffuse texture. KN5 export fails with a null `txDiffuse` error. Common
offenders: Bezier curves, GCP empties, AC marker empties. Assign a `Null`
placeholder material to any such object before export.

### Null material

A material named `Null` must exist (or be created) in the scene. It is assigned
to objects that need a slot but should not render. The placement script creates
it automatically if missing (`use_nodes=False`).

### Texture paths

Blender materials use `TEX_IMAGE` nodes pointing to textures in the `texture/`
subfolder beside the blend file. Paths must be relative: `//texture\<filename>`.
Absolute paths (baked to another machine's location) cause purple/magenta
textures. If textures appear purple after copying a template, remap with:

```python
for img in bpy.data.images:
    img.filepath = f'//texture\\{os.path.basename(img.filepath)}'
```

### Blender materials vs. ksEditor shaders

Blender materials are **viewport preview only**. The actual in-game appearance
is controlled by ksEditor shaders, assigned after FBX import:

- Shader: `ksPerPixel` (or `ksPerPixelMultiMap` for PBR)
- Diffuse texture assigned to `txDiffuse` slot
- Required for every material: Cone, RubberBlack, ROAD, GRASS, etc.

---

## data/surfaces.ini

Maps object name prefixes to physics surface types.

```ini
[SURFACE_0]
KEY=ROAD
FRICTION=0.98
DAMPING=0
IS_VALID_TRACK=1
DIRT_ADDITIVE=0
...

[SURFACE_1]
KEY=GRASS
FRICTION=0.65
DAMPING=0.1
IS_VALID_TRACK=0
...
```

The object prefix number (`1ROAD0`, `1GRASS0`) refers to the surface index in
this file. Objects prefixed `1ROAD*` get SURFACE_0 physics; `1GRASS*` get
SURFACE_1.

---

## FBX Export Settings

When exporting from Blender for ksEditor:

- **Scale:** `FBX All` — any other setting causes wrong in-game scale
- **Object types:** Mesh + Empty
- Before exporting: confirm every object has a material slot; delete or
  assign Null to any curves or guide objects

---

## Flat vs. Raycast Templates

| | Flat | Raycast |
|---|---|---|
| Example template | `rem_gymkhana` | `seneca_runway` |
| Cone Z placement | Z = 0 (constant) | BVH raycast against road/grass meshes |
| Terrain | Single plane at Z=0 | Real elevation mesh from NPZ data |
| NPZ files | Not needed | Required (`dtm_full.npz`, `pavement_mesh.npz`, etc.) |
| Road scaling | Mesh scaled to fit course bounds + 30 m padding | Not applicable |
