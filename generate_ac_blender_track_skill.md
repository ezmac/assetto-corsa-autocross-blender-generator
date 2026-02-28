Your goal is to make blender scenes that are usable for the driving in the simulator/game "Assetto Corsa".  To do that, you want to convert an overhead map image to a blender file in a specific format that can be converted using an external tool called kseditor.  your goal is to take an image that represents an autocross track and convert it into a 1:1 scale replica of that course.  You will start with a template.  Never modify the template. Copy the template as your work, open blender, and use the blender tool to modify the copy.  Ask questions if you have trouble identifying something.

Autocross uses road cones to define a technical course.  This makes placement and orientation important.  When mapped, there should be a scale and some representation of cones.  Some cones are lying on their side pointing but most will be standing.  Lying cones are sometimes triangles or arrows, standing cones are sometimes circles or squares.  There may be a legend in the image.

The template is rem_gymkhana.
Template path: C:\Users\tad\ax_maps\rem_gymkhana\Blender\Project\AC-GYMKHANA.blend

---

## Learnings from Seneca Grand Prix 2021 build

### Scale Determination
- Course maps often include a grid as scale reference. Look for the legend — it will name the grid size (e.g. "20' squares").
- Detect the grid lines in the image using blue-channel analysis: `(B - R > 15) & (B > 180) & (R > 150)`. Find rows or columns where nearly the full image width is blue. Measure the pixel spacing between those lines.
- Formula: `scale_m_per_px = grid_size_meters / grid_spacing_px`  (e.g. 20ft = 6.096m / 34px = 0.1793 m/px)
- **Do not guess scale from cone cluster sizes — always measure from the grid.**

### Cone Detection
- Use `PIL` + `scipy.ndimage.label` on orange pixels: `(R > 180) & (G > 80) & (G < 200) & (B < 100) & (R > G + 50)`, minimum component size 3px.
- Raw detection finds multiple sub-clusters within each orange dot. **Merge clusters within ~40px (about 1.2 grid squares) of each other** using a greedy weighted-centroid merge. This reduces duplicates (e.g. 161 raw → 82 actual cones).
- Coordinate conversion: `bx = (px - X_CENTER) * SCALE`, `by = -(py - Y_CENTER) * SCALE` (Y axis is inverted between image and Blender).
- X_CENTER and Y_CENTER are the pixel coordinates of the course center (midpoint of detected cone bounds).

### Object Naming (AC/ksEditor format)
- `1ROAD0` — flat driveable surface at Z=0. Must cover entire course + margin.
- `1WALL0` — collision wall surrounding the course. Inner edge ~10m beyond cone extents.
- `Terrain` — flat ground plane at Z=-0.5 (slightly below road), covers entire area.
- `AC_POBJECT_SCONE_NNN` — standing cone instances (upright, NNN is zero-padded index). `AC_POBJECT_` prefix makes them collidable/movable in AC.
- `AC_POBJECT_PCONE_NNN` — pointer cone instances (lying flat, tip points in facing direction).
- **Each `AC_POBJECT_` cone must have its own unique mesh data.** Use `scone_tmpl.data.copy()` when creating instances — do NOT pass `scone_tmpl.data` directly. Objects sharing mesh data are treated as instances by the FBX exporter; AC cannot make them individually movable/collidable. Verified: `obj.data.users` should be `1` for every cone.
- `KSTREE_GROUP_A_*` — decorative trees. Place outside the wall boundary.
- `StudiumLight_*` — stadium lights. Place outside wall at corners.
- `AC_PIT_0`, `AC_START_0`, `AC_HOTLAP_START_0` — spawn/timing markers.

### AC Marker Setup
- **Rotation must be (-90°, 0°, 0°) in Blender Euler XYZ** — this matches the template and produces correct car orientation in AC's Y-up coordinate system. Zero rotation causes the car to spawn facing the sky.
- Place all three markers (PIT, START, HOTLAP) at the start line position for autocross (non-circuit events have no separate pit lane).
- Z height: 0.5m above road surface.
- Start line: look for a green marker/stripe in the image. Finish: look for red marker.

### Terrain
- The template terrain is a **ring mesh** (donut shape for the original oval gymkhana track). **Replace it** with a solid 4-vertex flat quad at Z=-0.5 covering ±(course_extent + 50m). Do this with bmesh:
  ```python
  bm.clear()
  half = desired_half_size
  verts = [bm.verts.new(co) for co in [(-half,-half,-0.5),(half,-half,-0.5),(half,half,-0.5),(-half,half,-0.5)]]
  bm.faces.new(verts)
  ```
- Reset terrain scale to (1,1,1) with transform_apply before rebuilding.

### Wall Construction
- Build as 4 separate box panels (N/S/E/W) using bmesh. For non-square courses, use separate inner_x/inner_y dimensions.
- Run `bpy.ops.mesh.normals_make_consistent(inside=False)` after building.
- Set `wall.rotation_euler = (0,0,0)` before editing — bmesh can inherit stale rotation.

### FBX Export
- **Use scale setting "FBX All" when exporting** — do not use the default scale. Using the wrong scale setting was confirmed to cause in-game scale errors.
- The ksEditor export button is in the AC Materials panel in Blender's sidebar.
- **Every object in the scene must have at least one Blender material assigned**, even if it's just a Null/placeholder material. Objects with no material slots get assigned `FBX_MATERIAL` by ksEditor, which has no `txDiffuse` — KN5 export will fail with "null texture txDiffuse".
- Common offenders: Bezier curve/spline objects used as path guides (BézierCurve, infieldcurve, etc.) often have no material. Either assign Null material or delete/hide them before FBX export.
- Set `hide_render = True` on all AC marker objects (AC_PIT_0, AC_START_0, AC_HOTLAP_START_0, AC_TIME_*_L/R, GCP objects). They must be invisible in-game.
- All AC marker objects must also have a material assigned (Null/placeholder) to avoid FBX_MATERIAL error.

### ksEditor Material/Shader Setup
- **Blender material nodes are NOT used by ksEditor for visual shading.** The Principled BSDF + TEX_IMAGE setup in Blender is only for preview purposes.
- After importing the FBX into ksEditor, you must assign shaders and textures in ksEditor's own interface:
  - Select a mesh object in ksEditor
  - In the Materials panel, set Shader to `ksPerPixel` (or `ksPerPixelMultiMap` for PBR)
  - Assign the diffuse texture to the `txDiffuse` slot
- Do this for every material: Cone, RubberBlack, ROAD, GRASS, etc.
- You do NOT need any custom properties on Blender materials for ksEditor to work.

### ac_tools Addon (Blender)
- The `ac_tools` addon (`bl_ext.blender_org.ac_tools`) is for **physics surface naming only**.
- It renames objects with prefixes like `1ROAD`, `1GRASS`, `1WALL`, `1KERB` which the AC physics engine reads to assign surface properties (friction, FFB, etc.).
- It loads custom surface definitions from `//ac_track/data/surfaces.ini` (path relative to the .blend file). This folder must exist for the addon to function.
- **It does NOT configure ksEditor visual shaders or textures.** Do not use it expecting any visual result.
- ac_tools operators just do: `obj.name = prefix + '.' + obj.name`

### Scripts on Disk — Use These, Not Manual Steps

| Script | Location | Purpose |
|--------|----------|---------|
| `detect_cones.py` | `C:\Users\tad\clawdmaps\` | Image → `cone_data_affine.json` (affine GCP transform, color detection, merge, facing angles) |
| `place_cones.py` | `C:\Users\tad\clawdmaps\` | JSON → Blender scene (BVH raycast Z, `.data.copy()` per cone, AC marker material/hide fixes, save) |

**Correct workflow:**
1. Run `detect_cones.py` with system Python to produce `cone_data_affine.json`
2. Copy the template to `generated/<track_name>/` and open it in Blender
3. Run `place_cones.py` from Blender's Scripting tab — it handles everything and saves
4. Manually export FBX: File → Export → FBX, scale = **FBX All**
5. In ksEditor: import FBX, assign `ksPerPixel` shader + `txDiffuse` texture per material, export KN5

Do **not** place cones manually via the MCP tool or interactively in Blender — the scripts encode all the correct settings (naming, mesh independence, raycasting, marker fixes).

### Python Environment
- Use system Python 3.14 at `C:\Users\tad\AppData\Local\Python\pythoncore-3.14-64\python.exe` for image analysis scripts run via subprocess from Blender.
- Do NOT use the venv version — it is not guaranteed to work.
- Required packages: `pillow`, `scipy`, `numpy` (install with `python -m pip install pillow scipy numpy`).


## Elements
Element is a generic term for a group of cones.  An element is thought of collectively, like the 5 cones in a slalom.  If there are pointer cones, those are part of the element.  

The spacing of pointer cones is to help vision.  They should be close to their standing cone.  Pointer cones are sometimes used for "walls" to separate the course visually, though standing cones are used as well.  Pointer cones should be 3 inches from the standing cone or the next pointer cone unless otherwise stated.


Here are some element patterns:
pointer >
standing X
pin cone >X
double pointer >>X
triple pointer >>>X
cone wall XXXXXXXXXX
pointer wall >>>>>>>>
chicago box X    X    X
            X         X
            XXXXXXXXXXX
Chicago boxes are walls of cones in a box shape where the center of the open side has a cone.  They would be 
gate X   X
Gates are cones you drive between. Start gate has two cones on either side and should include the hotlap start marker, the pit marker, and the hotlap start marker.  A timing start gate should include the timing start markers and is a single cone per side.  Finish should have two standing cones and the timing stop markers.
slalom X     X     X     X
A slalom has a pointer to indicate entry side. Slaloms are spaced equally.  Slaloms can be offset on alternating sides of the centerline.


When asked for a specific element, create a group for that element in blender, then add the appropriate cones to that group so the user can move them.  Generate the element on the user's cursor.  

