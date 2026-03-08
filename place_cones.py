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
  7. Place timing gate empties (AC_AB_START_L/R, AC_AB_FINISH_L/R) from green/red cone positions
  8. Move spawn markers (AC_PIT_0, AC_START_0, AC_HOTLAP_START_0) behind the start gate,
     oriented to face through it, at Z + 1.5 m above road surface
  9. Ensure all AC marker objects (timing, spawn, GCP) have a material slot (prevents
     ksEditor FBX_MATERIAL / null txDiffuse export error)
 10. Set hide_render=True on all AC marker and GCP objects (they must be invisible in-game)
 11. Verify mesh independence (users=1 per cone)
 12. Save the blend file
"""

import bpy
import json
import math
import os
import numpy as np
from mathutils.bvhtree import BVHTree
from mathutils import Vector, Matrix

# ── Configuration ─────────────────────────────────────────────────────────────

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _here_p = bpy.data.filepath
    _HERE = os.path.dirname(_here_p)
    while _here_p and _here_p != os.path.dirname(_here_p):
        _here_p = os.path.dirname(_here_p)
        if os.path.isfile(os.path.join(_here_p, 'place_cones.py')):
            _HERE = _here_p
            break

JSON_PATH = os.path.join(_HERE, "source_images", "cone_data_affine.json")

# GCP scene object names in the same order as blues[0], blues[1], blues[2]
# when a JSON has 3 blue entries (hand-authored files use this for alignment).
GCP_NAMES = [
    "AC_POBJECT_GCP_P_TOP_LEFT",
    "AC_POBJECT_GCP_P_TOP_RIGHT",
    "AC_POBJECT_GCP_P_BOTTOM_RIGHT",
]

# Mesh objects to raycast against for Z height (tried in order, first hit wins)
RAYCAST_MESHES = ["1ROAD0", "1ROAD1", "1ROAD2", "1GRASS0", "1GRASS1"]

# Template cone — all placed cones use a copy of this object's mesh data.
# Must match the working V2 / MovableCone dimensions (~7.5" base, ~26" tall).
# The smaller 17" StaticCone was too narrow at bumper height for AC's physics
# to reliably detect standing cones.
TEMPLATE_CONE_NAME = "AC_POBJECT_MovableCone"

# Standing cones: place base at road surface (no vertical offset).
# AC_POBJECT_ physics requires the body to be in contact with the road mesh
# to initialise.  Any positive lift floats the cone above the surface and
# physics never triggers.  The raw cone mesh sits ~1 mm below its local
# origin, so z = road_surface gives ~1 mm of road contact — matching the
# working V2 cones which also had their base just at road level.
STANDING_Z_OFFSET = 0.0

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
greens   = data.get("timing_start", [])   # timing start gate cones (2 expected)
reds     = data.get("timing_end",   [])   # timing end gate cones   (2 expected)

print(f"Loaded: {len(standing)} standing, {len(pointers)} pointer cones")

# ── GCP affine alignment (hand-authored JSON files) ───────────────────────────
#
# If the JSON has 3 blue GCP entries AND all 3 GCP scene objects exist, solve
# a 6-parameter affine transform from the JSON's coordinate space to Blender
# world space and remap all cone positions before placement.
#
# blues[0] → GCP_NAMES[0] (TOP_LEFT)
# blues[1] → GCP_NAMES[1] (TOP_RIGHT)
# blues[2] → GCP_NAMES[2] (BOTTOM_RIGHT)
#
# For image-detected JSON files the bx/by values are already in Blender world
# space (transform was applied in detect_cones.py), so blues will be absent or
# the scene GCPs won't form a solvable system — the block is skipped.

blues = data.get("gcp", [])
if len(blues) == 3:
    scene_pts = []
    for name in GCP_NAMES:
        obj = bpy.data.objects.get(name)
        if obj is None:
            print(f"GCP object '{name}' not found — skipping affine alignment")
            scene_pts = []
            break
        scene_pts.append((obj.location.x, obj.location.y))

    if len(scene_pts) == 3:
        src = np.array([[b["bx"], b["by"]] for b in blues], dtype=float)
        dst = np.array(scene_pts, dtype=float)
        P  = np.column_stack([src, np.ones(3)])
        ax = np.linalg.solve(P, dst[:, 0])
        ay = np.linalg.solve(P, dst[:, 1])

        def affine(bx, by):
            return (ax[0]*bx + ax[1]*by + ax[2],
                    ay[0]*bx + ay[1]*by + ay[2])

        for c in standing + pointers + greens + reds:
            c["bx"], c["by"] = affine(c["bx"], c["by"])

        print(f"GCP affine applied: blues → {[n.split('_')[-1] for n in GCP_NAMES]}")
        print(f"  ax={[round(v,6) for v in ax]}  ay={[round(v,6) for v in ay]}")
else:
    print(f"No GCP blues in JSON — using bx/by as Blender world coords")

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
# Hide the template so it isn't exported to FBX / KN5 as its own physics body
tmpl.hide_render = True
print(f"Template: {tmpl.name}  mesh={tmpl.data.name}  "
      f"mats={[m.name for m in tmpl.data.materials]}")

# Derive pointer placement geometry directly from the mesh so this stays
# correct if the cone is ever rescaled.
#
# The cone origin sits at the base center (local Z ≈ 0, tip at local +Z).
# When a pointer cone lies flat (Rx = π/2), local Y maps to world Z, so
# the base circle (radius r) projects ±r vertically — half the cone is
# buried in the surface.
#
# Natural resting tilt: arctan(r/h).  At this angle the tip and the bottom
# of the base rim both touch the surface simultaneously, and the Z offset
# needed to lift the cone onto the surface is r·cos(tilt).
_verts = tmpl.data.vertices
CONE_BASE_RADIUS = max(abs(v.co.x) for v in _verts)    # e.g. 0.1397 m
CONE_HEIGHT      = max(v.co.z      for v in _verts)    # e.g. 0.4312 m
POINTER_TILT     = math.atan2(CONE_BASE_RADIUS, CONE_HEIGHT)   # ≈ 17.9°
POINTER_Z_OFFSET = CONE_BASE_RADIUS * math.cos(POINTER_TILT)   # ≈ 0.133 m
print(f"Cone geometry: base_r={CONE_BASE_RADIUS:.4f}  height={CONE_HEIGHT:.4f}  "
      f"tilt={math.degrees(POINTER_TILT):.1f}°  z_offset={POINTER_Z_OFFSET:.4f} m")

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
    obj.location       = (x, y, z + STANDING_Z_OFFSET)
    obj.rotation_euler = (0, 0, 0)
    obj.scale          = (1, 1, 1)
    collection.objects.link(obj)

print(f"Placed {len(standing)} standing cones  ({s_misses} Z misses → fallback z=0)")

# ── Place pointer cones ───────────────────────────────────────────────────────
#
# Pointer cones lie flat on their side with the tip pointing toward the nearest
# standing cone.  facing_deg is already in Blender world space (Y not flipped,
# stored by detect_cones.py as atan2 in Blender coords).
#
# Blender rotation to lay a cone flat with tip in facing_deg direction,
# tilted at the natural resting angle so both the tip and the bottom of
# the base rim touch the surface:
#   rotation_euler = (π/2 + POINTER_TILT,  0,  radians(facing_deg + 90))
#
# Z is raycasted to the surface then raised by POINTER_Z_OFFSET so the
# lowest contact point (tip and base-rim bottom) sits exactly on the mesh.
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
    obj.location       = (x, y, z + POINTER_Z_OFFSET)
    obj.rotation_euler = (math.pi / 2 + POINTER_TILT, 0, rot_z)
    obj.scale          = (1, 1, 1)
    collection.objects.link(obj)

print(f"Placed {len(pointers)} pointer cones  ({p_misses} Z misses → fallback z=0)")

# ── Apply rotation/scale to all placed cones ──────────────────────────────────
#
# AC's physics engine for AC_POBJECT_ objects computes a convex hull from the
# mesh vertices.  If rotation is stored on the OBJECT (not baked into the mesh),
# the hull may be computed in local space — giving an upright-cone shape for
# pointer cones that are visually lying flat, and the physics engine may not
# properly register standing cones whose base is flush with the road.
#
# Baking rotation into the mesh vertices (equivalent to Ctrl+A → Apply Rotation)
# ensures the physics hull matches the final world-space cone shape.
# Standing cones: rotation is already identity, so no vertex change.
# Pointer cones: vertices are transformed to the lying-flat orientation.

all_placed = [o for o in bpy.data.objects if "SCONE" in o.name or "PCONE" in o.name]
for obj in all_placed:
    rot_scale = obj.matrix_basis.to_3x3().to_4x4()  # rotation+scale as 4x4, no translation
    obj.data.transform(rot_scale)                   # bake into mesh vertices
    obj.rotation_euler = (0, 0, 0)
    obj.scale          = (1, 1, 1)
print(f"Applied rotation/scale to {len(all_placed)} cone meshes")

# ── Place timing gates and spawn markers ──────────────────────────────────────
#
# Green cones → AC_AB_START_L / AC_AB_START_R  (A-to-B timing start gate)
# Red cones   → AC_AB_FINISH_L / AC_AB_FINISH_R (A-to-B timing finish gate)
#
# Autocross has a separate start and finish gate, making it an A-to-B type track
# per the AC naming convention (not circuit-type AC_TIME_0/1).  The template
# already contains AC_AB_START/FINISH MESH objects at placeholder positions;
# these are relocated.  Any AC_TIME_0/1 empties left by a previous run are
# deleted.
#
# Left/right is assigned from the driver's perspective entering the gate.
# Entry direction = perpendicular to gate line pointing toward course interior.
#
# Spawn markers are placed SPAWN_BACK_M behind the start gate, 1.5 m above road.
# Car heading: atan2(entry_dir.x, entry_dir.y) — empirically matches AC convention.

SPAWN_BACK_M = 5.0   # metres behind start gate for spawn markers

def place_or_move(name, x, y, z_offset=0.0, rot=(0.0, 0.0, 0.0)):
    """Create an Empty at (x,y) or move an existing object there."""
    z = (get_z(x, y) or 0.0) + z_offset
    obj = bpy.data.objects.get(name)
    if obj:
        obj.location = (x, y, z)
        obj.rotation_euler = rot
    else:
        obj = bpy.data.objects.new(name, None)   # Empty — no geometry
        obj.location = (x, y, z)
        obj.rotation_euler = rot
        collection.objects.link(obj)
    return obj

if len(greens) == 2 and len(reds) == 2:
    g0 = Vector((greens[0]["bx"], greens[0]["by"], 0.0))
    g1 = Vector((greens[1]["bx"], greens[1]["by"], 0.0))
    r0 = Vector((reds[0]["bx"],   reds[0]["by"],   0.0))
    r1 = Vector((reds[1]["bx"],   reds[1]["by"],   0.0))

    g_mid = (g0 + g1) * 0.5
    r_mid = (r0 + r1) * 0.5

    # Course centroid: mean XY of all standing cones
    cent_x = sum(c["bx"] for c in standing) / len(standing)
    cent_y = sum(c["by"] for c in standing) / len(standing)
    centroid = Vector((cent_x, cent_y, 0.0))

    # Entry direction: perpendicular to the gate line, toward the course interior
    gate_line = (g1 - g0).normalized()
    perp_ccw = Vector((-gate_line.y,  gate_line.x, 0.0))
    perp_cw  = Vector(( gate_line.y, -gate_line.x, 0.0))
    entry_dir = perp_ccw if perp_ccw.dot(centroid - g_mid) > 0 else perp_cw

    # Left/right: from driver facing entry_dir, left = rotate +90° CCW
    left_dir = Vector((-entry_dir.y, entry_dir.x, 0.0))

    def is_left(cone_dict, midpoint):
        v = Vector((cone_dict["bx"] - midpoint.x, cone_dict["by"] - midpoint.y, 0.0))
        return v.dot(left_dir) > 0

    g_L, g_R = (greens[0], greens[1]) if is_left(greens[0], g_mid) else (greens[1], greens[0])
    r_L, r_R = (reds[0],   reds[1])   if is_left(reds[0],   r_mid) else (reds[1],   reds[0])

    # Remove any AC_TIME_0/1 empties left by a previous run
    for stale_name in ("AC_TIME_0_L", "AC_TIME_0_R", "AC_TIME_1_L", "AC_TIME_1_R"):
        stale = bpy.data.objects.get(stale_name)
        if stale and stale.type == 'EMPTY':
            bpy.data.objects.remove(stale, do_unlink=True)

    place_or_move("AC_AB_START_L",  g_L["bx"], g_L["by"])
    place_or_move("AC_AB_START_R",  g_R["bx"], g_R["by"])
    place_or_move("AC_AB_FINISH_L", r_L["bx"], r_L["by"])
    place_or_move("AC_AB_FINISH_R", r_R["bx"], r_R["by"])

    # Spawn markers: behind the gate, facing into the course
    # z_rot = atan2(entry_dir.x, entry_dir.y) matches Blender→AC heading convention
    spawn_x   = g_mid.x - entry_dir.x * SPAWN_BACK_M
    spawn_y   = g_mid.y - entry_dir.y * SPAWN_BACK_M
    z_rot     = math.atan2(entry_dir.x, entry_dir.y)
    spawn_rot = (-math.pi / 2, 0.0, z_rot)
    for mname in ("AC_PIT_0", "AC_START_0", "AC_HOTLAP_START_0"):
        place_or_move(mname, spawn_x, spawn_y, z_offset=1.5, rot=spawn_rot)

    print(f"Timing + spawn markers placed")
    print(f"  AB_START:  L=({g_L['bx']:.2f},{g_L['by']:.2f})  "
          f"R=({g_R['bx']:.2f},{g_R['by']:.2f})")
    print(f"  AB_FINISH: L=({r_L['bx']:.2f},{r_L['by']:.2f})  "
          f"R=({r_R['bx']:.2f},{r_R['by']:.2f})")
    print(f"  Entry dir:   ({entry_dir.x:.3f},{entry_dir.y:.3f})")
    print(f"  Spawn:       ({spawn_x:.2f},{spawn_y:.2f})  heading={math.degrees(z_rot):.1f}°")
else:
    print(f"WARNING: need 2 green + 2 red cones for timing markers, "
          f"got {len(greens)}g {len(reds)}r — skipped")

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

    # Empties (obj.data is None) have no material slots — skip material assignment
    if obj.data is not None and len(obj.material_slots) == 0:
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
