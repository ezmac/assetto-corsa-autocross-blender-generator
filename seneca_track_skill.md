# Seneca Runway Track Skill — Assetto Corsa Autocross Map Generation

## Overview

Goal: convert an overhead autocross map image into a 1:1 scale Assetto Corsa track using Blender and ksEditor.

Autocross uses road cones to define a course. Placement and orientation matter. In map images:
- Standing cones appear as circles or squares
- Pointer (lying) cones appear as triangles or arrows pointing toward their standing cone
- There may be a legend identifying cone types

**Never modify the template.** Always copy it to a new project folder before opening in Blender.

---

## Templates

### Active Template — seneca_runway
- **Path:** `C:\Users\tad\clawdmaps\templates\seneca_runway\seneca_runway.blend`
- Contains: runway pavement (`1ROAD0/1/2`), grass (`1GRASS0/1`), GCP reference objects, StaticCone template
- **Cone dimensions (in template):** 11" base (0.2794m) × 17" tall (0.4318m)

### Template Folder Structure
When copying to a new project, copy the full template folder contents — not just the blend file:
```
data/
extension/
texture/
ui/
*.npz
seneca_runway.blend
```
If only the blend file is copied, textures will be purple (broken relative paths). The `texture/` folder must be adjacent to the blend file.

---

## GCP Calibration (seneca_runway) — 3-Point Affine

Ground Control Points map image pixel coordinates to Blender world coordinates. Three points are required for a full affine transform (corrects scale, rotation, and shear).

| GCP Object | Blender (X, Y) | Image (px, py) | Notes |
|---|---|---|---|
| `AC_POBJECT_GCP_P_TOP_LEFT` | (-37.185, 105.608) | (11371, 4248) | |
| `AC_POBJECT_GCP_P_TOP_RIGHT` | (154.726, 109.324) | (19734, 4246) | |
| `AC_POBJECT_GCP_P_BOTTOM_RIGHT` | (246.058, -96.129) | (23730, 13437) | East bump-out corner, ~45° |

**Why 3 points:** A 2-point transform only corrects scale and offset. The 3rd GCP at a non-collinear position corrects rotation, which caused ~10m drift at the far western end of the course with 2-point calibration.

GCP objects in the template have `hide_render=True` and a Null material. They are reference-only and must not appear in-game.

---

## Cone Detection — detect_cones.py

**Script:** `C:\Users\tad\clawdmaps\detect_cones.py`
**Python:** `C:\Users\tad\AppData\Local\Python\pythoncore-3.14-64\python.exe` (system Python, not venv)
**Run:**
```
python detect_cones.py --image <source_image.png> --out source_images/cone_data_affine.json
```

**Use the `_magenta_pointers.png` image variant** — standing cones are orange, pointer cones are magenta (separately detectable). This prevents pointer/standing merging.

### Color Masks
| Type | Mask | Notes |
|---|---|---|
| Standing (orange) | `(R>180) & (G>80) & (G<200) & (B<100) & (R-G>50)` | |
| Pointer (magenta) | `(R>180) & (G<80) & (B>180)` | Detected separately |
| Timing start (green) | `(G>100) & (R<180) & (B<180) & (G-R>30) & (G-B>20)` | Exclude orange+magenta |
| Timing end (red) | `(R>150) & (G<100) & (B<100)` | Exclude orange+magenta |
| GCP (blue) | `(B>150) & (R<120) & (G<150) & (B-R>50)` | |

### Merge Radii
| Type | Radius | Rationale |
|---|---|---|
| Standing cones | 10px | Safe below 12-ft gate (~18.7px at working scale) |
| Pointer cones | 4px | Safe below 3" pointer spacing (~8px at working scale) |
| Green/red | 5px | |
| Blue GCP | 10px | |

### Affine Transform
`build_transform()` uses the 3 GCP pairs to compute a 6-parameter 2D affine:
```
bx = ax[0]*px + ax[1]*py + ax[2]
by = ay[0]*px + ay[1]*py + ay[2]
```
Output JSON keys: `standing`, `pointers`, `greens`, `reds`, `blues`, `transform`

### Pointer Facing Angle
- `facing_deg = atan2(nearest_standing_by - pointer_by, nearest_standing_bx - pointer_bx)` in Blender coordinate space
- Used directly as rotation in `place_cones.py`

### Seneca GP 2021 Results (magenta variant)
- 110 standing, 51 pointer, 2 green, 2 red, 3 blue (GCPs)

---

## Cone Placement — place_cones.py

**Script:** `C:\Users\tad\clawdmaps\place_cones.py`
**Run from:** Blender Scripting tab (or MCP tool via `exec(open(...).read())`)

### What It Does
1. Loads `cone_data_affine.json`
2. Builds BVH from `1ROAD0/1/2` + `1GRASS0/1` meshes for Z raycasting
3. Deletes existing `AC_POBJECT_SCONE_*` and `AC_POBJECT_PCONE_*` objects
4. Places 110 standing cones — upright, Z raycasted to road surface
5. Places 51 pointer cones — lying flat, rotated to face nearest standing cone, Z raycasted
6. Assigns Null material to any AC marker with no material slot
7. Sets `hide_render=True` on all AC markers and GCP objects
8. Saves the blend file

### Critical: Mesh Independence
Each cone gets its own mesh data copy:
```python
obj = bpy.data.objects.new(name, tmpl.data.copy())  # .copy() is required
```
**Do NOT use `tmpl.data` directly.** Objects sharing mesh data become FBX instances — AC cannot treat them as independent physics bodies. Verify with `obj.data.users == 1` per cone.

### Pointer Cone Rotation
```python
rotation_euler = (math.pi / 2, 0, math.radians(facing_deg + 90))
```
This lays the cone on its side with the tip pointing in `facing_deg` direction.

### Z Raycasting
- Cast ray from Z=100 downward; first hit on road/grass meshes sets the cone Z
- Road surface varies: ~1.2m at P-loop, ~-0.5m at far western end (real airport grade)
- Fallback to Z=0 if no hit (logged as a miss — investigate if count is nonzero)

---

## Object Naming (AC/ksEditor Format)

| Name Pattern | Purpose |
|---|---|
| `1ROAD0`, `1ROAD1`, `1ROAD2` | Driveable surface (physics: asphalt) |
| `1GRASS0`, `1GRASS1` | Grass terrain (physics: grass) |
| `1WALL0` | Collision wall |
| `Terrain` | Flat ground plane at Z=-0.5 |
| `AC_POBJECT_SCONE_NNN` | Standing cone — collidable/movable in AC |
| `AC_POBJECT_PCONE_NNN` | Pointer cone — collidable/movable in AC |
| `AC_PIT_0` | Pit spawn marker |
| `AC_START_0` | Race start marker |
| `AC_HOTLAP_START_0` | Hotlap start marker |
| `AC_TIME_0_L`, `AC_TIME_0_R` | Timing start gate (left/right) |
| `AC_TIME_1_L`, `AC_TIME_1_R` | Timing end gate (left/right) |

### AC Marker Setup
- Spawn markers rotation: `(-90°, 0°, 0°)` in Blender Euler XYZ — zero rotation causes the car to spawn facing the sky
- Place all three spawn markers (PIT, START, HOTLAP) at the start line — autocross has no separate pit lane
- Z height: ~1.5m above road surface
- All AC markers: `hide_render=True`, must have at least one material slot

---

## Materials

### Blender Materials (Preview Only)
Blender materials use Principled BSDF + TEX_IMAGE nodes. These are **for viewport preview only** — ksEditor does not use them for visual shading.

**Every object must have at least one material slot.** Objects with no material get assigned `FBX_MATERIAL` by ksEditor, which has no `txDiffuse` — KN5 export fails with "null texture txDiffuse".

Common offenders with no material:
- Bezier curve / spline guide objects (`BézierCurve`, `infieldcurve`, etc.)
- AC marker empty objects
- GCP reference objects

Fix: assign a `Null` placeholder material to any materialless object, or delete the object before export.

### ksEditor Shader Setup (Required After FBX Import)
ksEditor has its own material/shader system, configured in its interface after importing the FBX:
1. Select a mesh in ksEditor
2. In the Materials panel, set Shader: `ksPerPixel` (or `ksPerPixelMultiMap` for PBR)
3. Assign diffuse texture to `txDiffuse` slot
4. Repeat for every material: Cone, RubberBlack, ROAD, GRASS, etc.

### ac_tools Addon
The `ac_tools` Blender addon (`bl_ext.blender_org.ac_tools`) is for **physics surface naming only**. It renames objects with prefixes (`1ROAD`, `1GRASS`, `1WALL`, `1KERB`) that the AC physics engine reads from `//ac_track/data/surfaces.ini`. It has no effect on visual rendering or ksEditor shaders.

---

## FBX Export

- **File → Export → FBX, scale setting = "FBX All"** — any other scale setting causes in-game scale errors
- Export object types: MESH + EMPTY
- Before exporting: confirm every object has a material slot, confirm curves/guides are deleted or have Null material

---

## Full Workflow

```
1. Run detect_cones.py (system Python)
   → source_images/cone_data_affine.json

2. Create new project from template (renames blend, updates ui_track.json):
   python new_project.py <track_name>
   → generated/<track_name>/<track_name>.blend

3. Open <track_name>.blend in Blender

4. Run place_cones.py from Scripting tab
   → places 161 cones, raycasts Z, fixes markers, saves

5. Manually place timing/spawn markers at start/finish line

6. File → Export → FBX (scale: FBX All)

7. ksEditor: import FBX → assign ksPerPixel + txDiffuse per material → export KN5
```

Do **not** place cones manually via MCP or interactively — the scripts encode all correct settings.

### Scripts on Disk

| Script | Purpose |
|---|---|
| `detect_cones.py` | Image → `cone_data_affine.json` |
| `new_project.py` | Copy template, rename blend, update `ui_track.json` |
| `place_cones.py` | JSON → Blender scene (cones, raycast Z, marker fixes, save) |

All scripts live at `C:\Users\tad\clawdmaps\` and use system Python (`C:\Users\tad\AppData\Local\Python\pythoncore-3.14-64\python.exe`).

---

## Cone Dimensions

Standard autocross cone: **11" base (0.2794m) × 17" tall (0.4318m)**

The seneca_runway template StaticCone is set to these dimensions. To rescale:
```python
# Scale mesh vertices directly (applies permanently to the mesh)
bm = bmesh.new()
bm.from_mesh(tmpl.data)
for v in bm.verts:
    v.co.x *= scale_xy
    v.co.y *= scale_xy
    v.co.z *= scale_z
bm.to_mesh(tmpl.data)
bm.free()
tmpl.data.update()
```
After rescaling, re-run `place_cones.py` — placed cones copy the mesh at placement time.

---

## Elements

An element is a group of cones treated collectively. Elements have pointer cones as part of them when applicable. Pointer cones should be 3 inches from their standing cone or adjacent pointer unless otherwise specified.

When asked to create an element, make a Blender group, add the cones to it, and generate it at the user's cursor position.

### Element Patterns

```
pointer        >
standing       X
pin cone       >X
double pointer >>X
triple pointer >>>X
cone wall      XXXXXXXXXX
pointer wall   >>>>>>>>
chicago box    X    X    X
               X         X
               XXXXXXXXXXX
gate           X   X
slalom         X     X     X     X
```

**Chicago box:** Walls of cones in a box shape; the center of the open side has a cone.

**Gate:** Two cones you drive between. Start gate includes hotlap/pit/start markers. Timing gate is one cone per side with timing markers. Finish has two standing cones and timing stop markers.

**Slalom:** Equally spaced cones with a pointer indicating entry side. Alternating cones may be offset on either side of the centerline.

---

## Blender Python Notes

- `bpy.data.objects.new(name, mesh_data)` — creates object without linking; link with `collection.objects.link(obj)`
- `bpy.context.temp_override(window, screen, area, region)` — required for viewport operators
- `bpy.ops.wm.save_mainfile()` — save current file
- `bpy.ops.wm.open_mainfile(filepath=...)` — open a file (closes current)
- BVH raycast: `BVHTree.FromPolygons(verts, polys)` then `bvh.ray_cast(origin, direction)`
- Required packages for detection scripts: `pillow`, `scipy`, `numpy`
