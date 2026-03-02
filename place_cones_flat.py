"""
place_cones_flat.py — Place cones in a flat (no-elevation) Blender scene.

Run from Blender Scripting tab:
    exec(open(r"<path-to-clawdmaps>\place_cones_flat.py").read())

Or configure JSON_PATH and BLEND_PATH at the top before running.

Expects a flat road surface at Z=0. No BVH raycasting needed.
Cone template must be named AC_POBJECT_MovableCone in the scene.
"""

import bpy, json, math, os

# ── Locate script directory ────────────────────────────────────────────────────
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # exec() context — walk up from blend file to find the scripts folder
    _p = bpy.data.filepath
    _HERE = os.path.dirname(_p)
    while _p and _p != os.path.dirname(_p):
        _p = os.path.dirname(_p)
        if os.path.isfile(os.path.join(_p, 'place_cones_flat.py')):
            _HERE = _p
            break

# ── Config ────────────────────────────────────────────────────────────────────
JSON_PATH = os.path.join(_HERE, "ax_scale_test.json")   # ← change per project
ROAD_Z    = 0.0  # flat surface — all cones at this Z

# ── Load data ─────────────────────────────────────────────────────────────────
with open(JSON_PATH) as f:
    data = json.load(f)

b  = data['bounds']
cx = (b['xmin'] + b['xmax']) / 2
cy = (b['ymin'] + b['ymax']) / 2
print(f"Course center: ({cx:.2f}, {cy:.2f})")
print(f"Course size:   {b['xmax']-b['xmin']:.1f}m x {b['ymax']-b['ymin']:.1f}m")

# ── Move and scale flat surface objects to cover the course ───────────────────
import bmesh as _bmesh

PADDING  = 30.0
course_w = b['xmax'] - b['xmin']
course_h = b['ymax'] - b['ymin']
req_w    = course_w + 2 * PADDING
req_h    = course_h + 2 * PADDING

for name in ['1ROAD0', '1WALL0', 'Terrain']:
    obj = bpy.data.objects.get(name)
    if obj is None:
        continue
    obj.location.x = cx
    obj.location.y = cy

    verts = obj.data.vertices
    if not verts:
        continue
    xs    = [v.co.x for v in verts]
    ys    = [v.co.y for v in verts]
    cur_w = max(xs) - min(xs)
    cur_h = max(ys) - min(ys)
    sx    = max(req_w / cur_w, 1.0) if cur_w > 0 else 1.0
    sy    = max(req_h / cur_h, 1.0) if cur_h > 0 else 1.0

    if sx > 1.0 or sy > 1.0:
        bm = _bmesh.new()
        bm.from_mesh(obj.data)
        for v in bm.verts:
            v.co.x *= sx
            v.co.y *= sy
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        print(f"Scaled {name}: x*{sx:.2f} y*{sy:.2f} -> {cur_w*sx:.0f}m x {cur_h*sy:.0f}m")
    else:
        print(f"Moved  {name}: {cur_w:.0f}m x {cur_h:.0f}m covers course OK")

# ── Reposition stadium lights at corners, angled toward course center ──────────
LIGHT_MARGIN = 20.0
hw = req_w / 2 + LIGHT_MARGIN
hh = req_h / 2 + LIGHT_MARGIN
corners = [(cx-hw, cy+hh), (cx+hw, cy+hh), (cx+hw, cy-hh), (cx-hw, cy-hh)]
lights = sorted([o for o in bpy.data.objects if 'StudiumLight' in o.name], key=lambda o: o.name)
for i, obj in enumerate(lights[:4]):
    lx, ly = corners[i]
    obj.location.x = lx
    obj.location.y = ly
    obj.rotation_euler.z = math.atan2(cy - ly, cx - lx)
print(f"Moved {min(len(lights), 4)} stadium lights to corners")

# ── Distribute trees around perimeter rectangle ────────────────────────────────
TREE_MARGIN = 15.0
tx0 = cx - req_w / 2 - TREE_MARGIN
tx1 = cx + req_w / 2 + TREE_MARGIN
ty0 = cy - req_h / 2 - TREE_MARGIN
ty1 = cy + req_h / 2 + TREE_MARGIN
tw, th = tx1 - tx0, ty1 - ty0
perim = 2 * (tw + th)
trees = sorted([o for o in bpy.data.objects if 'KSTREE' in o.name], key=lambda o: o.name)
n = len(trees)
if n:
    for i, obj in enumerate(trees):
        t = (i / n) * perim
        if t < tw:
            x, y = tx0 + t, ty0
        elif t < tw + th:
            x, y = tx1, ty0 + (t - tw)
        elif t < 2 * tw + th:
            x, y = tx1 - (t - tw - th), ty1
        else:
            x, y = tx0, ty1 - (t - 2 * tw - th)
        obj.location.x = x
        obj.location.y = y
        obj.location.z = 0.0
    print(f"Distributed {n} trees around {tw:.0f}m x {th:.0f}m perimeter")

# ── Ensure Null material exists (for AC markers) ───────────────────────────────
if 'Null' not in bpy.data.materials:
    null_mat = bpy.data.materials.new('Null')
    null_mat.use_nodes = False
else:
    null_mat = bpy.data.materials['Null']

# ── Grab cone template ─────────────────────────────────────────────────────────
tmpl = bpy.data.objects['AC_POBJECT_MovableCone']
col  = bpy.context.scene.collection

# Derive pointer geometry from the mesh so tilt stays correct if cone is rescaled.
# Natural resting tilt: both tip and base-rim bottom touch the surface.
# Z offset: lifts the cone origin so the lowest contact point sits at ROAD_Z.
_verts           = tmpl.data.vertices
CONE_BASE_RADIUS = max(abs(v.co.x) for v in _verts)
CONE_HEIGHT      = max(v.co.z      for v in _verts)
POINTER_TILT     = math.atan2(CONE_BASE_RADIUS, CONE_HEIGHT)
POINTER_Z_OFFSET = CONE_BASE_RADIUS * math.cos(POINTER_TILT)
print(f"Cone: base_r={CONE_BASE_RADIUS:.4f}  height={CONE_HEIGHT:.4f}  "
      f"tilt={math.degrees(POINTER_TILT):.1f}°  z_offset={POINTER_Z_OFFSET:.4f}m")

# ── Delete existing cones ──────────────────────────────────────────────────────
removed = 0
for o in list(bpy.data.objects):
    if o.name.startswith('AC_POBJECT_SCONE_') or o.name.startswith('AC_POBJECT_PCONE_'):
        bpy.data.objects.remove(o, do_unlink=True)
        removed += 1
print(f"Removed {removed} existing cones")

# ── Standing cones ─────────────────────────────────────────────────────────────
for i, c in enumerate(data['standing']):
    obj = bpy.data.objects.new(f'AC_POBJECT_SCONE_{i:03d}', tmpl.data.copy())
    col.objects.link(obj)
    obj.location       = (c['bx'], c['by'], ROAD_Z)
    obj.rotation_euler = (0, 0, 0)
    obj.hide_render    = True

print(f"Placed {len(data['standing'])} standing cones (AC_POBJECT_SCONE_NNN)")

# ── Pointer cones ──────────────────────────────────────────────────────────────
# rotation_euler = (pi/2 + POINTER_TILT, 0, radians(facing_deg + 90))
# Z raised by POINTER_Z_OFFSET so tip and base-rim bottom both rest on surface.
for i, c in enumerate(data['pointers']):
    obj = bpy.data.objects.new(f'AC_POBJECT_PCONE_{i:03d}', tmpl.data.copy())
    col.objects.link(obj)
    obj.location       = (c['bx'], c['by'], ROAD_Z + POINTER_Z_OFFSET)
    obj.rotation_euler = (math.pi/2 + POINTER_TILT, 0, math.radians(c.get('facing_deg', 0) + 90))
    obj.hide_render    = True

print(f"Placed {len(data['pointers'])} pointer cones (AC_POBJECT_PCONE_NNN)")

# ── Bake rotation into mesh vertices (required for AC physics hull) ────────────
all_placed = [o for o in bpy.data.objects if 'SCONE' in o.name or 'PCONE' in o.name]
for obj in all_placed:
    rot_scale = obj.matrix_basis.to_3x3().to_4x4()
    obj.data.transform(rot_scale)
    obj.rotation_euler = (0, 0, 0)
    obj.scale          = (1, 1, 1)
print(f"Baked rotation into {len(all_placed)} cone meshes")

# ── Spawn markers at start gate ────────────────────────────────────────────────
greens = data.get('greens', [])
if greens:
    gx = sum(g['bx'] for g in greens) / len(greens)
    gy = sum(g['by'] for g in greens) / len(greens)

    dx = greens[1]['bx'] - greens[0]['bx']
    dy = greens[1]['by'] - greens[0]['by']
    gate_len = math.hypot(dx, dy)
    # Perpendicular pointing into the course (more-negative Y = south)
    perp1 = ( dy/gate_len, -dx/gate_len)
    perp2 = (-dy/gate_len,  dx/gate_len)
    perp  = perp1 if perp1[1] < perp2[1] else perp2
    face_angle = math.atan2(perp[1], perp[0])
    rz = face_angle - math.pi/2  # so rz=0 faces +Y by default

    spawn_offsets = {'AC_PIT_0': 0, 'AC_START_0': -3, 'AC_HOTLAP_START_0': 3}
    for mname, lat in spawn_offsets.items():
        if mname in bpy.data.objects:
            m = bpy.data.objects[mname]
            m.location = (
                gx + dx/gate_len * lat - perp[0] * 5,
                gy + dy/gate_len * lat - perp[1] * 5,
                1.5,
            )
            m.rotation_euler = (-math.pi/2, 0, rz)
            if len(m.material_slots) == 0 and m.type == 'MESH':
                m.data.materials.append(null_mat)
            m.hide_render = True
            print(f"{mname}: ({m.location.x:.2f}, {m.location.y:.2f}) facing={math.degrees(rz):.1f}°")

# ── Timing gates ───────────────────────────────────────────────────────────────
def make_empty_marker(name):
    if name in bpy.data.objects:
        return bpy.data.objects[name]
    obj = bpy.data.objects.new(name, None)
    col.objects.link(obj)
    return obj

reds = data.get('reds', [])

if len(greens) >= 2:
    dx = greens[1]['bx'] - greens[0]['bx']
    dy = greens[1]['by'] - greens[0]['by']
    g_len = math.hypot(dx, dy)
    for side, cone, sign in [('L', greens[0], -1), ('R', greens[1], 1)]:
        m = make_empty_marker(f'AC_TIME_0_{side}')
        m.location = (cone['bx'] + sign * dx/g_len * 2,
                      cone['by'] + sign * dy/g_len * 2, 1.5)
        m.rotation_euler = (-math.pi/2, 0, 0)
        m.hide_render = True
        print(f"AC_TIME_0_{side}: ({m.location.x:.2f}, {m.location.y:.2f})")

if len(reds) >= 2:
    dx = reds[1]['bx'] - reds[0]['bx']
    dy = reds[1]['by'] - reds[0]['by']
    r_len = math.hypot(dx, dy)
    for side, cone, sign in [('L', reds[0], -1), ('R', reds[1], 1)]:
        m = make_empty_marker(f'AC_TIME_1_{side}')
        m.location = (cone['bx'] + sign * dx/r_len * 2,
                      cone['by'] + sign * dy/r_len * 2, 1.5)
        m.rotation_euler = (-math.pi/2, 0, 0)
        m.hide_render = True
        print(f"AC_TIME_1_{side}: ({m.location.x:.2f}, {m.location.y:.2f})")

# ── Save ───────────────────────────────────────────────────────────────────────
bpy.ops.wm.save_mainfile()
print("\nDone — file saved.")
