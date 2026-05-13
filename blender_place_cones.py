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
SPAWN_BACK_M           = 5.0  # metres behind start gate for spawn markers (fallback)
SPAWN_BACK_FROM_STAGE_M = 3.0  # metres behind stage_cone_pos for spawn markers

# ── Load JSON ─────────────────────────────────────────────────────────────────
with open(JSON_PATH) as f:
    data = json.load(f)

standing      = data.get('standing', [])
pointers      = data.get('pointers', [])
greens        = data.get('timing_start', [])
reds          = data.get('timing_end',   [])
start_gate    = data.get('timing_start_gate')   # {"a": [bx,by], "b": [bx,by]} or None
finish_gate   = data.get('timing_end_gate')
stage_cone_pos = data.get('stage_cone_pos')     # [bx, by] of cones 100-103 centroid, or None

# ── GCP affine alignment ──────────────────────────────────────────────────────
# If the JSON has 3 GCP entries and all 3 scene objects exist, solve a 6-parameter
# affine from JSON coords → Blender world space and remap all positions.
# blues[0] → TOP_LEFT, blues[1] → TOP_RIGHT, blues[2] → BOTTOM_RIGHT
_GCP_NAMES = [
    "AC_POBJECT_GCP_P_TOP_LEFT",
    "AC_POBJECT_GCP_P_TOP_RIGHT",
    "AC_POBJECT_GCP_P_BOTTOM_RIGHT",
]
_blues = data.get("gcp", [])
if len(_blues) == 3:
    import numpy as _np
    _scene_pts = []
    for _gname in _GCP_NAMES:
        _gobj = bpy.data.objects.get(_gname)
        if _gobj is None:
            print(f"GCP object '{_gname}' not found — skipping affine alignment")
            _scene_pts = []
            break
        _scene_pts.append((_gobj.location.x, _gobj.location.y))

    if len(_scene_pts) == 3:
        _src = _np.array([[g["bx"], g["by"]] for g in _blues], dtype=float)
        _dst = _np.array(_scene_pts, dtype=float)
        _P   = _np.column_stack([_src, _np.ones(3)])
        _ax  = _np.linalg.solve(_P, _dst[:, 0])
        _ay  = _np.linalg.solve(_P, _dst[:, 1])

        def _gcp_affine(bx, by):
            return (_ax[0]*bx + _ax[1]*by + _ax[2],
                    _ay[0]*bx + _ay[1]*by + _ay[2])

        for _c in standing + pointers + greens + reds:
            _c["bx"], _c["by"] = _gcp_affine(_c["bx"], _c["by"])
        if stage_cone_pos:
            stage_cone_pos[0], stage_cone_pos[1] = _gcp_affine(stage_cone_pos[0], stage_cone_pos[1])
        if start_gate:
            start_gate["a"][0], start_gate["a"][1] = _gcp_affine(*start_gate["a"])
            start_gate["b"][0], start_gate["b"][1] = _gcp_affine(*start_gate["b"])
        if finish_gate:
            finish_gate["a"][0], finish_gate["a"][1] = _gcp_affine(*finish_gate["a"])
            finish_gate["b"][0], finish_gate["b"][1] = _gcp_affine(*finish_gate["b"])
        print(f"GCP affine applied: {[n.split('_')[-1] for n in _GCP_NAMES]}")
        print(f"  ax={[round(v,6) for v in _ax]}  ay={[round(v,6) for v in _ay]}")
elif _blues:
    print(f"GCP: {len(_blues)} entries found (need exactly 3) — skipping affine alignment")
else:
    print("No GCP data in JSON — using bx/by as Blender world coords directly")

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

    # If the JSON has page dimensions (solonats), size the road to the full page and
    # center it at the page center in world space — this makes UV mapping exact and
    # lets east/west courses share the same template without scale drift.
    _t = data.get('transform', {})
    if _t.get('page_w_pt') and _t.get('page_h_pt'):
        _sc      = _t.get('scale', 0.3048)
        _ox      = _t.get('ox',    0.0)
        _oy      = _t.get('oy',    0.0)
        req_w    = round(_t['page_w_pt']  * _sc, 3)
        req_h    = round(_t['page_h_pt'] * _sc, 3)
        # Page center in world space: ox + page_w/2*scale, oy - page_h/2*scale
        road_cx  = _ox + req_w / 2
        road_cy  = _oy - req_h / 2
        # Expand road if stage + spawn buffer falls outside the page extents.
        # UV mapping is world-position-based so expanding beyond the page is safe.
        if stage_cone_pos:
            _stage_pad = SPAWN_BACK_FROM_STAGE_M + 5.0
            _need_w = (abs(stage_cone_pos[0] - road_cx) + _stage_pad) * 2
            _need_h = (abs(stage_cone_pos[1] - road_cy) + _stage_pad) * 2
            if _need_w > req_w or _need_h > req_h:
                req_w = max(req_w, _need_w)
                req_h = max(req_h, _need_h)
                print(f"  Expanded road to cover stage area: {req_w:.0f}m x {req_h:.0f}m")
    else:
        PADDING  = 30.0
        # Expand bounds to include stage cone + spawn buffer before sizing the road.
        if stage_cone_pos:
            _stage_pad = SPAWN_BACK_FROM_STAGE_M + 5.0
            b = dict(b)
            b['xmin'] = min(b['xmin'], stage_cone_pos[0] - _stage_pad)
            b['xmax'] = max(b['xmax'], stage_cone_pos[0] + _stage_pad)
            b['ymin'] = min(b['ymin'], stage_cone_pos[1] - _stage_pad)
            b['ymax'] = max(b['ymax'], stage_cone_pos[1] + _stage_pad)
            cx = (b['xmin'] + b['xmax']) / 2
            cy = (b['ymin'] + b['ymax']) / 2
        req_w    = (b['xmax'] - b['xmin']) + 2 * PADDING
        req_h    = (b['ymax'] - b['ymin']) + 2 * PADDING
        road_cx  = cx
        road_cy  = cy

    # In flat mode, remove gymkhana-template grass meshes — Terrain covers the background.
    if FLAT:
        for name in ('1GRASS0', '1GRASS1'):
            obj = bpy.data.objects.get(name)
            if obj:
                bpy.data.objects.remove(obj, do_unlink=True)
                print(f"  Removed {name} (flat course)")

    for name in ['1ROAD0', '1WALL0', 'Terrain']:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        obj.location.x = road_cx
        obj.location.y = road_cy
        if name == '1ROAD0':
            obj.location.z = 0.002
        elif FLAT and name == 'Terrain':
            obj.location.z = 0.0   # clear any template origin offset; vertex z set below

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

        if sx > 1.0 or sy > 1.0 or (FLAT and name == 'Terrain'):
            bm = _bmesh.new()
            bm.from_mesh(obj.data)
            for v in bm.verts:
                v.co.x *= sx
                v.co.y *= sy
                if FLAT and name == 'Terrain':
                    v.co.z = -0.30   # flatten real-terrain elevation for flat courses
            bm.to_mesh(obj.data)
            bm.free()
            obj.data.update()
            if sx > 1.0 or sy > 1.0:
                print(f"  Scaled {name}: x*{sx:.2f} y*{sy:.2f} -> {cur_w*sx:.0f}m x {cur_h*sy:.0f}m")
            if FLAT and name == 'Terrain':
                print(f"  Terrain: flattened vertex Z to -0.30 (flat course)")
        else:
            print(f"  {name}: {cur_w:.0f}m x {cur_h:.0f}m fits course ({req_w:.0f}m x {req_h:.0f}m needed)")

    # ── Stadium lights: place at 4 corners of the wall, angled inward ────────
    LIGHT_MARGIN = 20.0   # metres outside the wall edge
    hw = req_w / 2 + LIGHT_MARGIN
    hh = req_h / 2 + LIGHT_MARGIN
    corners = [
        (road_cx - hw, road_cy + hh),   # upper-left
        (road_cx + hw, road_cy + hh),   # upper-right
        (road_cx + hw, road_cy - hh),   # lower-right
        (road_cx - hw, road_cy - hh),   # lower-left
    ]
    lights = sorted([o for o in bpy.data.objects if 'StudiumLight' in o.name],
                    key=lambda o: o.name)
    for i, obj in enumerate(lights[:4]):
        lx, ly = corners[i]
        obj.location = (lx, ly, obj.location.z)
        obj.rotation_euler.z = math.atan2(road_cy - ly, road_cx - lx)
    if lights:
        print(f"Placed {min(len(lights),4)} stadium lights at wall corners")

    # ── Trees: distribute evenly around a rectangle outside the wall ──────────
    TREE_MARGIN = 15.0    # metres outside the wall edge
    tx0 = road_cx - req_w / 2 - TREE_MARGIN
    tx1 = road_cx + req_w / 2 + TREE_MARGIN
    ty0 = road_cy - req_h / 2 - TREE_MARGIN
    ty1 = road_cy + req_h / 2 + TREE_MARGIN
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

def _gate_endpoints(gate_data, fallback_cones):
    """Return (Vector_a, Vector_b) for a timing gate.

    Uses explicit bar endpoints from gate_data if available, otherwise falls
    back to the two timing cone positions.  Returns (None, None) if neither.
    """
    from mathutils import Vector
    if gate_data:
        a = gate_data["a"]
        b = gate_data["b"]
        return Vector((a[0], a[1], 0)), Vector((b[0], b[1], 0))
    if len(fallback_cones) >= 2:
        c0, c1 = fallback_cones[0], fallback_cones[1]
        return Vector((c0['bx'], c0['by'], 0)), Vector((c1['bx'], c1['by'], 0))
    return None, None


has_start_gate  = start_gate  is not None or len(greens) >= 2
has_finish_gate = finish_gate is not None or len(reds)   >= 2

if has_start_gate and has_finish_gate:
    from mathutils import Vector

    g0, g1 = _gate_endpoints(start_gate,  greens)
    r0, r1 = _gate_endpoints(finish_gate, reds)
    g_mid = (g0 + g1) * 0.5

    def interior_perp(pa, pb, ref_x=None, ref_y=None):
        """Return the perpendicular to (pa→pb) pointing toward (ref_x, ref_y).

        Defaults to the course centroid when no reference is provided.
        """
        if ref_x is None:
            ref_x, ref_y = cx, cy
        mid     = (pa + pb) * 0.5
        bar_vec = (pb - pa).normalized()
        perp    = Vector((-bar_vec.y, bar_vec.x, 0))
        to_ref  = Vector((ref_x - mid.x, ref_y - mid.y, 0))
        return perp if to_ref.dot(perp) > 0 else -perp

    def gate_lr(pa, pb, travel_dir):
        """Return (left, right) endpoints given the driver's travel direction."""
        mid      = (pa + pb) * 0.5
        left_vec = Vector((-travel_dir.y, travel_dir.x, 0))
        return (pa, pb) if (pa - mid).dot(left_vec) > 0 else (pb, pa)

    # Start: entry = away from stage cones (100-103) if available, else toward centroid
    if stage_cone_pos:
        # Stage is behind the start gate; negate so entry points away from stage (into course)
        entry = -interior_perp(g0, g1, ref_x=stage_cone_pos[0], ref_y=stage_cone_pos[1])
        print(f"  Start direction from stage_cone_pos ({stage_cone_pos[0]:.1f},{stage_cone_pos[1]:.1f})")
    else:
        entry = interior_perp(g0, g1)
        print(f"  Start direction from centroid ({cx:.1f},{cy:.1f})")
    gL, gR = gate_lr(g0, g1, entry)

    # Finish: driver exits away from interior (toward centroid = interior)
    finish_exit = -interior_perp(r0, r1)
    rL, rR      = gate_lr(r0, r1, finish_exit)

    # Remove stale AC_TIME empties from old runs
    for n in ('AC_TIME_0_L', 'AC_TIME_0_R', 'AC_TIME_1_L', 'AC_TIME_1_R'):
        stale = bpy.data.objects.get(n)
        if stale and stale.type == 'EMPTY':
            bpy.data.objects.remove(stale, do_unlink=True)

    place_marker('AC_AB_START_L',  gL.x, gL.y)
    place_marker('AC_AB_START_R',  gR.x, gR.y)
    place_marker('AC_AB_FINISH_L', rL.x, rL.y)
    place_marker('AC_AB_FINISH_R', rR.x, rR.y)

    z_rot = math.atan2(entry.x, entry.y)
    rot   = (-math.pi/2, 0.0, z_rot)
    if stage_cone_pos:
        spawn_x = stage_cone_pos[0] - entry.x * SPAWN_BACK_FROM_STAGE_M
        spawn_y = stage_cone_pos[1] - entry.y * SPAWN_BACK_FROM_STAGE_M
        print(f"  Spawn at stage_cone_pos ({stage_cone_pos[0]:.1f},{stage_cone_pos[1]:.1f})")
    else:
        spawn_x = g_mid.x - entry.x * SPAWN_BACK_M
        spawn_y = g_mid.y - entry.y * SPAWN_BACK_M
    for n in ('AC_PIT_0', 'AC_START_0', 'AC_HOTLAP_START_0'):
        place_marker(n, spawn_x, spawn_y, z_offset=1.5, rot=rot)

    print(f"Timing + spawn markers placed  heading={math.degrees(z_rot):.1f}°")
    print(f"  Start: L=({gL.x:.1f},{gL.y:.1f})  R=({gR.x:.1f},{gR.y:.1f})")
    print(f"  Finish: L=({rL.x:.1f},{rL.y:.1f})  R=({rR.x:.1f},{rR.y:.1f})")

elif has_start_gate:
    from mathutils import Vector
    g0, g1 = _gate_endpoints(start_gate, greens)
    g_mid  = (g0 + g1) * 0.5
    gate_vec = (g1 - g0).normalized()
    # Pick perpendicular pointing generally toward lower Y (course usually below start)
    perp1 = Vector((-gate_vec.y, gate_vec.x, 0))
    perp2 = -perp1
    entry = perp1 if perp1.y < 0 else perp2
    rz    = math.atan2(entry.x, entry.y)
    rot   = (-math.pi/2, 0, rz)

    if stage_cone_pos:
        spawn_x = stage_cone_pos[0] - entry.x * SPAWN_BACK_FROM_STAGE_M
        spawn_y = stage_cone_pos[1] - entry.y * SPAWN_BACK_FROM_STAGE_M
        print(f"  Spawn at stage_cone_pos ({stage_cone_pos[0]:.1f},{stage_cone_pos[1]:.1f})")
    else:
        spawn_x = g_mid.x - entry.x * SPAWN_BACK_M
        spawn_y = g_mid.y - entry.y * SPAWN_BACK_M
    for n in ('AC_PIT_0', 'AC_START_0', 'AC_HOTLAP_START_0'):
        place_marker(n, spawn_x, spawn_y, z_offset=1.5, rot=rot)
    place_marker('AC_AB_START_L', g0.x, g0.y)
    place_marker('AC_AB_START_R', g1.x, g1.y)
    print(f"Start gate + spawn markers placed (no end gate)  heading={math.degrees(rz):.1f}°")

else:
    if stage_cone_pos:
        # No timing gates but we have a staging area — point spawn toward course centroid.
        from mathutils import Vector
        to_cx   = Vector((cx - stage_cone_pos[0], cy - stage_cone_pos[1], 0)).normalized()
        rz      = math.atan2(to_cx.x, to_cx.y)
        spawn_x = stage_cone_pos[0] - to_cx.x * SPAWN_BACK_FROM_STAGE_M
        spawn_y = stage_cone_pos[1] - to_cx.y * SPAWN_BACK_FROM_STAGE_M
        for n in ('AC_PIT_0', 'AC_START_0', 'AC_HOTLAP_START_0'):
            place_marker(n, spawn_x, spawn_y, z_offset=1.5, rot=(-math.pi/2, 0, rz))
        print(f"  Spawn at stage_cone_pos (no gates)  heading={math.degrees(rz):.1f}°")
    else:
        print("No timing markers (no gate data in JSON)")

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

# ── Chalk texture on road surface ────────────────────────────────────────────
_chalk_src  = os.path.splitext(os.path.abspath(JSON_PATH))[0] + '_chalk.png'
_blend_dir  = os.path.dirname(os.path.abspath(bpy.data.filepath))
_tex_dir    = os.path.join(_blend_dir, 'texture')
_chalk_name = os.path.basename(_chalk_src)
_chalk_path = os.path.join(_tex_dir, _chalk_name)   # canonical in-project copy

if os.path.isfile(_chalk_src) and '1ROAD0' in bpy.data.objects:
    # Copy chalk PNG into blender/texture/ and convert to DDS for ksEditor
    os.makedirs(_tex_dir, exist_ok=True)
    if not os.path.isfile(_chalk_path) or os.path.getmtime(_chalk_src) > os.path.getmtime(_chalk_path):
        import shutil as _shutil
        _shutil.copy2(_chalk_src, _chalk_path)
        print(f"Copied chalk PNG → {_chalk_path}")

    _chalk_dds_name = os.path.splitext(_chalk_name)[0] + '.dds'
    _chalk_dds_path = os.path.join(_tex_dir, _chalk_dds_name)
    if not os.path.isfile(_chalk_dds_path) or os.path.getmtime(_chalk_path) > os.path.getmtime(_chalk_dds_path):
        import subprocess as _sp
        _r = _sp.run(['convert', _chalk_path, _chalk_dds_path], capture_output=True)
        if _r.returncode == 0:
            print(f"Converted chalk PNG → DDS: {_chalk_dds_path}")
        else:
            print(f"WARNING: chalk DDS conversion failed: {_r.stderr.decode().strip()}")
            _chalk_dds_path = _chalk_path   # fall back to PNG for viewport

    road = bpy.data.objects['1ROAD0']
    mesh = road.data
    t      = data.get('transform', {})
    ox     = t.get('ox',        0.0)
    oy     = t.get('oy',        0.0)
    scale  = t.get('scale',     0.3048)
    page_w = t.get('page_w_pt', 1.0)
    page_h = t.get('page_h_pt', 1.0)

    # Write chalk UVs into the first UV layer (UVMap) so ksEditor picks them up.
    # Remove any extra layers first; ksEditor always reads layer 0.
    while len(mesh.uv_layers) > 1:
        mesh.uv_layers.remove(mesh.uv_layers[-1])
    uv_layer = mesh.uv_layers[0] if mesh.uv_layers else mesh.uv_layers.new(name='UVMap')
    uv_layer.name = 'UVMap'
    mesh.uv_layers.active = uv_layer

    # Force depsgraph update so matrix_world reflects any location changes made above.
    bpy.context.view_layer.update()
    mat_world = road.matrix_world
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            wp = mat_world @ mesh.vertices[v_idx].co
            # Blender world (m) → PDF page coords (pt); y is flipped in pdf_to_blender
            pdf_x = (wp.x - ox) / scale
            pdf_y = (oy - wp.y) / scale
            uv_layer.data[loop_idx].uv = (pdf_x / page_w, 1.0 - pdf_y / page_h)

    # Wire road material to chalk DDS (Blender loads DDS fine for viewport too)
    img = bpy.data.images.load(_chalk_dds_path, check_existing=True)
    img.filepath = f'//texture/{os.path.basename(_chalk_dds_path)}'
    mat = mesh.materials[0] if mesh.materials else bpy.data.materials.new('ROAD')
    if not mesh.materials:
        mesh.materials.append(mat)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out  = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    tex  = nt.nodes.new('ShaderNodeTexImage')
    uv_n = nt.nodes.new('ShaderNodeUVMap')
    tex.image   = img
    uv_n.uv_map = 'chalk'
    nt.links.new(uv_n.outputs['UV'],     tex.inputs['Vector'])
    nt.links.new(tex.outputs['Color'],   bsdf.inputs['Base Color'])
    nt.links.new(bsdf.outputs['BSDF'],   out.inputs['Surface'])
    print(f"Chalk texture applied to 1ROAD0: {_chalk_dds_path}")
else:
    if not os.path.isfile(_chalk_src):
        print(f"No chalk PNG found at {_chalk_src}, skipping texture")
    elif '1ROAD0' not in bpy.data.objects:
        print("No 1ROAD0 object found, skipping chalk texture")

# ── FBX export ────────────────────────────────────────────────────────────────
if FBX_PATH:
    _fbx_dir = os.path.dirname(os.path.abspath(FBX_PATH))
    os.makedirs(_fbx_dir, exist_ok=True)

    # Make all image paths absolute before export so Blender can find them to copy
    for _img in bpy.data.images:
        if _img.filepath:
            _img.filepath = os.path.abspath(bpy.path.abspath(_img.filepath))

    # Temporarily unlink the cone template so it is excluded from the FBX.
    # Re-link afterwards so the .blend stays valid for future runs.
    _tmpl_obj = bpy.data.objects.get('AC_POBJECT_MovableCone')
    _tmpl_cols = list(_tmpl_obj.users_collection) if _tmpl_obj else []
    for _c in _tmpl_cols:
        _c.objects.unlink(_tmpl_obj)

    _fbx_abs = os.path.abspath(FBX_PATH)
    bpy.ops.export_scene.fbx(
        filepath=_fbx_abs,
        object_types={'MESH', 'EMPTY'},
        apply_scale_options='FBX_SCALE_ALL',
        use_selection=False,
        path_mode='COPY',
        embed_textures=False,
    )
    print(f"FBX exported: {FBX_PATH}")

    for _c in _tmpl_cols:
        _c.objects.link(_tmpl_obj)

# ── Restore Windows-relative image paths before saving .blend ─────────────────
# Only touch images with absolute paths; relative paths (starting //) are already correct.
for _img in bpy.data.images:
    if _img.filepath and not _img.filepath.startswith('//'):
        _img.filepath = f'//texture/{os.path.basename(_img.filepath)}'

# ── Save ──────────────────────────────────────────────────────────────────────
bpy.ops.wm.save_mainfile()
print(f"Saved: {bpy.data.filepath}")
