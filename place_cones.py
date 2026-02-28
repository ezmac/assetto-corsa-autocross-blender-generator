"""
place_cones.py — Blender script to place cones from cone_data_affine.json.

Run from Blender's Scripting workspace, or paste into the Blender MCP tool.

Steps performed:
  1. Load cone positions from JSON (affine-corrected Blender coordinates)
  2. Build a BVH raycast tree from road/grass meshes for Z height snapping
  3. Delete any existing AC_POBJECT_SCONE_* and AC_POBJECT_PCONE_* objects
  4. Place 110 standing cones (upright, Z raycasted)
  5. Place 51 pointer cones (lying flat, rotated to face nearest standing cone, Z raycasted)
  6. Each cone gets its own mesh data copy — required for independent AC physics bodies
  7. Ensure all AC marker objects (timing, spawn, GCP) have a material slot (prevents
     ksEditor FBX_MATERIAL / null txDiffuse export error)
  8. Set hide_render=True on all AC marker and GCP objects (they must be invisible in-game)
  9. Verify mesh independence (users=1 per cone)
 10. Save the blend file
"""

import bpy
import json
import math
from mathutils.bvhtree import BVHTree
from mathutils import Vector

# ── Configuration ─────────────────────────────────────────────────────────────

JSON_PATH = r"C:\Users\tad\clawdmaps\source_images\cone_data_affine.json"

# Mesh objects to raycast against for Z height (tried in order, first hit wins)
RAYCAST_MESHES = ["1ROAD0", "1ROAD1", "1ROAD2", "1GRASS0", "1GRASS1"]

# Template cone — all placed cones use a copy of this object's mesh data
TEMPLATE_CONE_NAME = "StaticCone"

# AC marker objects that must be hidden in-game and have a material slot
# (ksEditor fails to export KN5 if any object has no material)
AC_MARKER_PREFIXES = [
    "AC_PIT_",
    "AC_START_",
    "AC_HOTLAP_START_",
    "AC_TIME_",
    "AC_AB_",
    "AC_POBJECT_GCP_",   # GCP reference objects used for image calibration
]

# ── Load JSON ─────────────────────────────────────────────────────────────────

with open(JSON_PATH) as f:
    data = json.load(f)

standing = data["standing"]   # list of {bx, by}
pointers = data["pointers"]   # list of {bx, by, facing_deg}

print(f"Loaded: {len(standing)} standing, {len(pointers)} pointer cones")

# ── Build BVH for raycasting ──────────────────────────────────────────────────

bvh_list = []
for name in RAYCAST_MESHES:
    obj = bpy.data.objects.get(name)
    if obj is None:
        continue
    mat = obj.matrix_world
    verts = [mat @ v.co for v in obj.data.vertices]
    polys  = [list(p.vertices) for p in obj.data.polygons]
    bvh_list.append(BVHTree.FromPolygons(verts, polys))
    print(f"  BVH built: {name}")

if not bvh_list:
    raise RuntimeError("No road/grass meshes found. Check RAYCAST_MESHES names match the scene.")

def get_z(x, y):
    """Cast a ray straight down from Z=100; return surface Z or None on miss."""
    origin    = Vector((x, y, 100.0))
    direction = Vector((0, 0, -1))
    for bvh in bvh_list:
        loc, _, _, _ = bvh.ray_cast(origin, direction)
        if loc is not None:
            return loc.z
    return None

# ── Get or verify template cone ───────────────────────────────────────────────

tmpl = bpy.data.objects.get(TEMPLATE_CONE_NAME)
if tmpl is None:
    raise RuntimeError(f"Template cone '{TEMPLATE_CONE_NAME}' not found in scene.")
print(f"Template: {tmpl.name}  mesh={tmpl.data.name}  "
      f"mats={[m.name for m in tmpl.data.materials]}")

collection = bpy.context.scene.collection

# ── Delete existing placed cones ──────────────────────────────────────────────

to_delete = [o for o in bpy.data.objects if "SCONE" in o.name or "PCONE" in o.name]
for o in to_delete:
    bpy.data.objects.remove(o, do_unlink=True)
print(f"Deleted {len(to_delete)} existing cone objects")

# ── Place standing cones ──────────────────────────────────────────────────────

s_misses = 0
for i, c in enumerate(standing):
    x, y = c["bx"], c["by"]
    z = get_z(x, y)
    if z is None:
        s_misses += 1
        z = 0.0

    obj = bpy.data.objects.new(f"AC_POBJECT_SCONE_{i:03d}", tmpl.data.copy())
    obj.location      = (x, y, z)
    obj.rotation_euler = (0, 0, 0)
    obj.scale         = (1, 1, 1)
    collection.objects.link(obj)

print(f"Placed {len(standing)} standing cones  ({s_misses} Z misses → fallback z=0)")

# ── Place pointer cones ───────────────────────────────────────────────────────
#
# Pointer cones lie flat on their side with the tip pointing toward the nearest
# standing cone.  facing_deg is already in Blender world space (Y not flipped,
# stored by detect_cones.py as atan2 in Blender coords).
#
# Blender rotation to lay a cone flat with tip in facing_deg direction:
#   rotation_euler = (π/2,  0,  radians(facing_deg + 90))
#

p_misses = 0
for i, c in enumerate(pointers):
    x, y = c["bx"], c["by"]
    z = get_z(x, y)
    if z is None:
        p_misses += 1
        z = 0.0

    facing_deg = c.get("facing_deg", 0.0)
    rot_z = math.radians(facing_deg + 90)

    obj = bpy.data.objects.new(f"AC_POBJECT_PCONE_{i:03d}", tmpl.data.copy())
    obj.location       = (x, y, z)
    obj.rotation_euler = (math.pi / 2, 0, rot_z)
    obj.scale          = (1, 1, 1)
    collection.objects.link(obj)

print(f"Placed {len(pointers)} pointer cones  ({p_misses} Z misses → fallback z=0)")

# ── Fix AC markers: material slot + hide_render ───────────────────────────────
#
# Every object exported to FBX must have at least one material slot.
# Objects with no material get assigned FBX_MATERIAL by ksEditor, which has
# no txDiffuse — KN5 export then fails with "null texture txDiffuse".
#
# AC marker objects also need hide_render=True so they are invisible in-game.
#

# Find or create a Null/placeholder material for markers
null_mat = bpy.data.materials.get("Null")
if null_mat is None:
    null_mat = bpy.data.materials.new("Null")
    print("Created 'Null' placeholder material")

fixed_markers = 0
for obj in bpy.data.objects:
    is_marker = any(obj.name.startswith(pfx) for pfx in AC_MARKER_PREFIXES)
    if not is_marker:
        continue

    # Ensure at least one material slot
    if len(obj.material_slots) == 0:
        obj.data.materials.append(null_mat)
        fixed_markers += 1

    # Hide in renders / game export
    obj.hide_render = True

print(f"AC markers fixed: {fixed_markers} got Null material; all set hide_render=True")

# ── Verify mesh independence ──────────────────────────────────────────────────

shared = [o for o in bpy.data.objects
          if ("SCONE" in o.name or "PCONE" in o.name) and o.data.users > 1]
if shared:
    print(f"WARNING: {len(shared)} cones still share mesh data — check for issues!")
else:
    print(f"OK: all {len(standing) + len(pointers)} cones have independent mesh data (users=1)")

# ── Save ──────────────────────────────────────────────────────────────────────

bpy.ops.wm.save_mainfile()
print(f"Saved: {bpy.data.filepath}")
