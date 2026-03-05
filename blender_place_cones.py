"""
blender_place_cones.py — Headless Blender script for cone placement and FBX export.

Invoked by build_track.py via:
    blender --background <file.blend> --python blender_place_cones.py -- --json <path> [--flat] [--fbx <path>]

Can also be run interactively from the Blender Scripting tab:
    exec(open("/path/to/assetto-corsa-autocross-blender-generator/blender_place_cones.py").read())
    (set JSON_PATH / FLAT / FBX_PATH below before running interactively)
"""

import bpy, json, math, sys, os

# ── Args: headless via '--' separator, or edit inline for interactive use ─────
if '--' in sys.argv:
    import argparse
    _p = argparse.ArgumentParser()
    _p.add_argument('--json',  required=True, help='Cone data JSON path')
    _p.add_argument('--flat',  action='store_true', help='Flat surface: no BVH raycast, Z=0')
    _p.add_argument('--fbx',   default=None, help='FBX output path (optional)')
    _a = _p.parse_args(sys.argv[sys.argv.index('--') + 1:])
    JSON_PATH = _a.json
    FLAT      = _a.flat
    FBX_PATH  = _a.fbx
else:
    # ── Interactive inline config ──────────────────────────────────────────────
    # Locate the scripts directory (works headless or via exec())
    try:
        _HERE = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        _here_p = bpy.data.filepath
        _HERE = os.path.dirname(_here_p)
        while _here_p and _here_p != os.path.dirname(_here_p):
            _here_p = os.path.dirname(_here_p)
            if os.path.isfile(os.path.join(_here_p, 'blender_place_cones.py')):
                _HERE = _here_p
                break
    JSON_PATH = os.path.join(_HERE, "ax_scale_test.json")   # ← change per project
    FLAT      = True
    FBX_PATH  = None

RAYCAST_MESHES = ['1ROAD0', '1ROAD1', '1ROAD2', '1GRASS0', '1GRASS1']
AC_MARKER_PREFIXES = ['AC_PIT_', 'AC_START_', 'AC_HOTLAP_START_',
                      'AC_TIME_', 'AC_AB_', 'AC_POBJECT_GCP_']
SPAWN_BACK_M = 5.0   # metres behind start gate for spawn markers

# ── Load JSON ─────────────────────────────────────────────────────────────────
with open(JSON_PATH) as f:
    data = json.load(f)

standing = data.get('standing', [])
pointers = data.get('pointers', [])
greens   = data.get('greens',   [])
reds     = data.get('reds',     [])

# Compute bounds if missing (old detect_cones.py format)
if 'bounds' not in data:
    all_pts = standing + pointers + greens + reds
    data['bounds'] = {
        'xmin': min(c['bx'] for c in all_pts),
        'xmax': max(c['bx'] for c in all_pts),
        'ymin': min(c['by'] for c in all_pts),
        'ymax': max(c['by'] for c in all_pts),
    }

b  = data['bounds']
cx = (b['xmin'] + b['xmax']) / 2
cy = (b['ymin'] + b['ymax']) / 2
print(f"JSON: {os.path.basename(JSON_PATH)}")
print(f"Course center ({cx:.1f}, {cy:.1f}), "
      f"size {b['xmax']-b['xmin']:.1f}m x {b['ymax']-b['ymin']:.1f}m")
print(f"Cones: {len(standing)} standing, {len(pointers)} pointer  "
      f"Markers: {len(greens)} green, {len(reds)} red  Mode: {'flat' if FLAT else 'raycast'}")

# ── Z lookup ──────────────────────────────────────────────────────────────────
if FLAT:
    import bmesh as _bmesh

    PADDING   = 30.0   # metres of road beyond the outermost cone on each side
    course_w  = b['xmax'] - b['xmin']
    course_h  = b['ymax'] - b['ymin']
    req_w     = course_w + 2 * PADDING
    req_h     = course_h + 2 * PADDING

    for name in ['1ROAD0', '1WALL0', 'Terrain']:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        obj.location.x = cx
        obj.location.y = cy

        # Scale mesh vertices so the object is at least req_w x req_h.
        # Compute current extents from local-space vertices.
        verts = obj.data.vertices
        if not verts:
            continue
        xs = [v.co.x for v in verts]
        ys = [v.co.y for v in verts]
        cur_w = max(xs) - min(xs)
        cur_h = max(ys) - min(ys)

        sx = max(req_w / cur_w, 1.0) if cur_w > 0 else 1.0
        sy = max(req_h / cur_h, 1.0) if cur_h > 0 else 1.0

        if sx > 1.0 or sy > 1.0:
            bm = _bmesh.new()
            bm.from_mesh(obj.data)
            for v in bm.verts:
                v.co.x *= sx
                v.co.y *= sy
            bm.to_mesh(obj.data)
            bm.free()
            obj.data.update()
            print(f"  Scaled {name}: x*{sx:.2f} y*{sy:.2f} -> {cur_w*sx:.0f}m x {cur_h*sy:.0f}m")
        else:
            print(f"  {name}: {cur_w:.0f}m x {cur_h:.0f}m fits course ({req_w:.0f}m x {req_h:.0f}m needed)")

    # ── Stadium lights: place at 4 corners of the wall, angled inward ────────
    LIGHT_MARGIN = 20.0   # metres outside the wall edge
    hw = req_w / 2 + LIGHT_MARGIN
    hh = req_h / 2 + LIGHT_MARGIN
    corners = [
        (cx - hw, cy + hh),   # upper-left
        (cx + hw, cy + hh),   # upper-right
        (cx + hw, cy - hh),   # lower-right
        (cx - hw, cy - hh),   # lower-left
    ]
    lights = sorted([o for o in bpy.data.objects if 'StudiumLight' in o.name],
                    key=lambda o: o.name)
    for i, obj in enumerate(lights[:4]):
        lx, ly = corners[i]
        obj.location = (lx, ly, obj.location.z)
        obj.rotation_euler.z = math.atan2(cy - ly, cx - lx)
    if lights:
        print(f"Placed {min(len(lights),4)} stadium lights at wall corners")

    # ── Trees: distribute evenly around a rectangle outside the wall ──────────
    TREE_MARGIN = 15.0    # metres outside the wall edge
    tx0 = cx - req_w / 2 - TREE_MARGIN
    tx1 = cx + req_w / 2 + TREE_MARGIN
    ty0 = cy - req_h / 2 - TREE_MARGIN
    ty1 = cy + req_h / 2 + TREE_MARGIN
    tw, th = tx1 - tx0, ty1 - ty0
    perim = 2 * (tw + th)

    trees = sorted([o for o in bpy.data.objects if 'KSTREE' in o.name],
                   key=lambda o: o.name)
    n = len(trees)
    if n:
        for i, obj in enumerate(trees):
            t = (i / n) * perim
            if t < tw:
                x, y = tx0 + t,        ty0
            elif t < tw + th:
                x, y = tx1,            ty0 + (t - tw)
            elif t < 2 * tw + th:
                x, y = tx1 - (t - tw - th), ty1
            else:
                x, y = tx0,            ty1 - (t - 2 * tw - th)
            obj.location.x = x
            obj.location.y = y
            obj.location.z = 0.0
        print(f"Placed {n} trees around {tw:.0f}m x {th:.0f}m perimeter "
              f"(spacing {perim/n:.1f}m)")

    def get_z(x, y):
        return 0.0

else:
    from mathutils.bvhtree import BVHTree
    from mathutils import Vector

    bvh_list = []
    for name in RAYCAST_MESHES:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        mat   = obj.matrix_world
        verts = [mat @ v.co for v in obj.data.vertices]
        polys = [list(p.vertices) for p in obj.data.polygons]
        bvh_list.append(BVHTree.FromPolygons(verts, polys))
        print(f"  BVH: {name}")

    if not bvh_list:
        raise RuntimeError("No road/grass meshes found for raycasting.")

    def get_z(x, y):
        origin    = Vector((x, y, 100.0))
        direction = Vector((0, 0, -1))
        for bvh in bvh_list:
            loc, _, _, _ = bvh.ray_cast(origin, direction)
            if loc is not None:
                return loc.z
        return None

# ── Cone template ─────────────────────────────────────────────────────────────
tmpl = bpy.data.objects.get('AC_POBJECT_MovableCone')
if tmpl is None:
    raise RuntimeError("AC_POBJECT_MovableCone not found in scene.")
tmpl.hide_render = True

# Derive pointer geometry from mesh (stays correct if cone is rescaled)
_v               = tmpl.data.vertices
CONE_BASE_RADIUS = max(abs(v.co.x) for v in _v)
CONE_HEIGHT      = max(v.co.z      for v in _v)
POINTER_TILT     = math.atan2(CONE_BASE_RADIUS, CONE_HEIGHT)
POINTER_Z_OFFSET = CONE_BASE_RADIUS * math.cos(POINTER_TILT)
print(f"Cone: base_r={CONE_BASE_RADIUS:.4f} h={CONE_HEIGHT:.4f} "
      f"tilt={math.degrees(POINTER_TILT):.1f}° z_off={POINTER_Z_OFFSET:.4f}m")

col = bpy.context.scene.collection

# ── Delete existing placed cones ──────────────────────────────────────────────
to_del = [o for o in bpy.data.objects if 'SCONE' in o.name or 'PCONE' in o.name]
for o in to_del:
    bpy.data.objects.remove(o, do_unlink=True)
print(f"Removed {len(to_del)} existing cones")

# ── Standing cones ────────────────────────────────────────────────────────────
s_miss = 0
for i, c in enumerate(standing):
    z = get_z(c['bx'], c['by'])
    if z is None:
        z = 0.0
        s_miss += 1
    obj = bpy.data.objects.new(f'AC_POBJECT_SCONE_{i:03d}', tmpl.data.copy())
    col.objects.link(obj)
    obj.location       = (c['bx'], c['by'], z)
    obj.rotation_euler = (0, 0, 0)
    obj.hide_render    = True

print(f"Placed {len(standing)} standing cones  ({s_miss} Z misses)")

# ── Pointer cones ─────────────────────────────────────────────────────────────
# rotation_euler = (pi/2 + POINTER_TILT, 0, radians(facing_deg + 90))
# Z raised by POINTER_Z_OFFSET so tip and base-rim bottom rest on surface.
p_miss = 0
for i, c in enumerate(pointers):
    z = get_z(c['bx'], c['by'])
    if z is None:
        z = 0.0
        p_miss += 1
    obj = bpy.data.objects.new(f'AC_POBJECT_PCONE_{i:03d}', tmpl.data.copy())
    col.objects.link(obj)
    obj.location       = (c['bx'], c['by'], z + POINTER_Z_OFFSET)
    obj.rotation_euler = (math.pi/2 + POINTER_TILT, 0,
                          math.radians(c.get('facing_deg', 0) + 90))
    obj.hide_render    = True

print(f"Placed {len(pointers)} pointer cones   ({p_miss} Z misses)")

# ── Bake rotation into mesh vertices (required for AC physics hull) ───────────
all_placed = [o for o in bpy.data.objects if 'SCONE' in o.name or 'PCONE' in o.name]
for obj in all_placed:
    rot_scale = obj.matrix_basis.to_3x3().to_4x4()
    obj.data.transform(rot_scale)
    obj.rotation_euler = (0, 0, 0)
    obj.scale          = (1, 1, 1)
print(f"Baked rotation into {len(all_placed)} cone meshes")

# ── Timing gates and spawn markers ────────────────────────────────────────────
def get_or_make_empty(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        obj = bpy.data.objects.new(name, None)
        col.objects.link(obj)
    return obj

def place_marker(name, x, y, z_offset=0.0, rot=(0.0, 0.0, 0.0)):
    z = (get_z(x, y) or 0.0) + z_offset
    obj = get_or_make_empty(name)
    obj.location       = (x, y, z)
    obj.rotation_euler = rot
    obj.hide_render    = True
    return obj

if len(greens) == 2 and len(reds) == 2:
    from mathutils import Vector

    g0 = Vector((greens[0]['bx'], greens[0]['by'], 0))
    g1 = Vector((greens[1]['bx'], greens[1]['by'], 0))
    r0 = Vector((reds[0]['bx'],   reds[0]['by'],   0))
    r1 = Vector((reds[1]['bx'],   reds[1]['by'],   0))
    g_mid = (g0 + g1) * 0.5

    # Entry direction: perpendicular to gate toward course centroid
    cent = Vector((sum(c['bx'] for c in standing) / max(len(standing), 1),
                   sum(c['by'] for c in standing) / max(len(standing), 1), 0))
    gate  = (g1 - g0).normalized()
    perp  = Vector((-gate.y, gate.x, 0))
    entry = perp if perp.dot(cent - g_mid) > 0 else -perp

    # Left/right from driver's perspective
    left = Vector((-entry.y, entry.x, 0))
    def is_left(c, mid):
        return Vector((c['bx'] - mid.x, c['by'] - mid.y, 0)).dot(left) > 0

    gL, gR = (greens[0], greens[1]) if is_left(greens[0], g_mid) else (greens[1], greens[0])
    rL, rR = (reds[0],   reds[1])   if is_left(reds[0],   (r0+r1)*0.5) else (reds[1], reds[0])

    # Remove stale AC_TIME empties from old runs
    for n in ('AC_TIME_0_L', 'AC_TIME_0_R', 'AC_TIME_1_L', 'AC_TIME_1_R'):
        stale = bpy.data.objects.get(n)
        if stale and stale.type == 'EMPTY':
            bpy.data.objects.remove(stale, do_unlink=True)

    place_marker('AC_AB_START_L',  gL['bx'], gL['by'])
    place_marker('AC_AB_START_R',  gR['bx'], gR['by'])
    place_marker('AC_AB_FINISH_L', rL['bx'], rL['by'])
    place_marker('AC_AB_FINISH_R', rR['bx'], rR['by'])

    spawn_x = g_mid.x - entry.x * SPAWN_BACK_M
    spawn_y = g_mid.y - entry.y * SPAWN_BACK_M
    z_rot   = math.atan2(entry.x, entry.y)
    rot     = (-math.pi/2, 0.0, z_rot)
    for n in ('AC_PIT_0', 'AC_START_0', 'AC_HOTLAP_START_0'):
        place_marker(n, spawn_x, spawn_y, z_offset=1.5, rot=rot)

    print(f"Timing + spawn markers placed  heading={math.degrees(z_rot):.1f}°")

elif len(greens) >= 2:
    # No reds — at least put timing start markers
    dx = greens[1]['bx'] - greens[0]['bx']
    dy = greens[1]['by'] - greens[0]['by']
    g_len = math.hypot(dx, dy)
    perp1 = ( dy/g_len, -dx/g_len)
    perp2 = (-dy/g_len,  dx/g_len)
    perp  = perp1 if perp1[1] < perp2[1] else perp2
    rz    = math.atan2(perp[1], perp[0]) - math.pi/2
    gx    = sum(g['bx'] for g in greens) / len(greens)
    gy    = sum(g['by'] for g in greens) / len(greens)

    for mname, lat in (('AC_PIT_0', 0), ('AC_START_0', -3), ('AC_HOTLAP_START_0', 3)):
        place_marker(mname,
                     gx + dx/g_len * lat - perp[0] * SPAWN_BACK_M,
                     gy + dy/g_len * lat - perp[1] * SPAWN_BACK_M,
                     z_offset=1.5, rot=(-math.pi/2, 0, rz))
    for side, cone, sign in (('L', greens[0], -1), ('R', greens[1], 1)):
        place_marker(f'AC_TIME_0_{side}',
                     cone['bx'] + sign * dx/g_len * 2,
                     cone['by'] + sign * dy/g_len * 2,
                     z_offset=1.5)
    print("Start gate + spawn markers placed (no end gate — reds missing)")

else:
    print("No timing markers (no green/red cones in data)")

# ── Fix AC markers: Null material + hide_render ───────────────────────────────
null_mat = bpy.data.materials.get('Null')
if null_mat is None:
    null_mat = bpy.data.materials.new('Null')
    null_mat.use_nodes = False

fixed = 0
for obj in bpy.data.objects:
    if not any(obj.name.startswith(p) for p in AC_MARKER_PREFIXES):
        continue
    if obj.data is not None and len(obj.material_slots) == 0:
        obj.data.materials.append(null_mat)
        fixed += 1
    obj.hide_render = True
print(f"AC markers: {fixed} got Null material, all set hide_render=True")

# ── Verify mesh independence ──────────────────────────────────────────────────
shared = [o for o in bpy.data.objects
          if ('SCONE' in o.name or 'PCONE' in o.name) and o.data.users > 1]
if shared:
    print(f"WARNING: {len(shared)} cones share mesh data")
else:
    print(f"OK: all {len(standing)+len(pointers)} cones have independent mesh data")

# ── FBX export ────────────────────────────────────────────────────────────────
if FBX_PATH:
    os.makedirs(os.path.dirname(os.path.abspath(FBX_PATH)), exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=os.path.abspath(FBX_PATH),
        object_types={'MESH', 'EMPTY'},
        apply_scale_options='FBX_SCALE_ALL',
        use_selection=False,
    )
    print(f"FBX exported: {FBX_PATH}")

# ── Save ──────────────────────────────────────────────────────────────────────
bpy.ops.wm.save_mainfile()
print(f"Saved: {bpy.data.filepath}")
