"""
_check_flat_scene.py — Blender subscript: validate a generated flat template scene.

Run by test_flat_template.py via:
    blender --background <file.blend> --python _check_flat_scene.py -- --width W --length L

Prints JSON to stdout: {"results": [{"name": "...", "ok": bool, "msg": "..."}, ...]}
"""

import sys
import json
import math
import argparse
import bpy

def parse_args():
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument('--width',  type=float, default=120.0)
    p.add_argument('--length', type=float, default=80.0)
    return p.parse_args(argv)

args = parse_args()
road_w = args.width
road_l = args.length

results = []

def check(name, ok, msg=''):
    results.append({'name': name, 'ok': bool(ok), 'msg': str(msg)})

def obj_exists(obj_name, expected_type=None):
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        check(f'{obj_name} exists', False, 'object not found in scene')
        return None
    if expected_type and obj.type != expected_type:
        check(f'{obj_name} exists', False,
              f'expected type {expected_type}, got {obj.type}')
        return None
    check(f'{obj_name} exists', True)
    return obj

def has_material_slot(obj):
    ok = len(obj.material_slots) >= 1
    check(f'{obj.name} has material slot', ok,
          f'{len(obj.material_slots)} slots' if not ok else '')

def mesh_bounds(obj):
    """Return (width_x, length_y) from local vertex extents."""
    verts = obj.data.vertices
    if not verts:
        return 0, 0
    xs = [v.co.x for v in verts]
    ys = [v.co.y for v in verts]
    return max(xs) - min(xs), max(ys) - min(ys)

# ── Required mesh objects ──────────────────────────────────────────────────────

road = obj_exists('1ROAD0', 'MESH')
if road:
    has_material_slot(road)
    w, l = mesh_bounds(road)
    check('1ROAD0 width matches --width',
          abs(w - road_w) < 0.01, f'got {w:.3f}m, expected {road_w}m')
    check('1ROAD0 length matches --length',
          abs(l - road_l) < 0.01, f'got {l:.3f}m, expected {road_l}m')
    check('1ROAD0 at Z=0',
          abs(road.location.z) < 0.001, f'Z={road.location.z}')
    mat_name = road.material_slots[0].name if road.material_slots else ''
    check('1ROAD0 material is ROAD', mat_name == 'ROAD', f'got {mat_name!r}')

grass = obj_exists('1GRASS0', 'MESH')
if grass:
    has_material_slot(grass)
    gw, gl = mesh_bounds(grass)
    check('1GRASS0 wider than road',  gw > road_w, f'{gw:.1f}m vs road {road_w}m')
    check('1GRASS0 longer than road', gl > road_l, f'{gl:.1f}m vs road {road_l}m')

terrain = obj_exists('Terrain', 'MESH')
if terrain:
    has_material_slot(terrain)
    # Terrain should be strictly below road (Z<0) to avoid z-fighting.
    # Check location.z; if it is 0.0 also check world-space vertex Z.
    tz = terrain.location.z
    if tz < -0.001:
        check('Terrain Z < 0 (below road)', True, f'Z={tz}')
    else:
        # Fallback: check world-space min vertex Z (transform may be baked)
        world_zs = [(terrain.matrix_world @ v.co).z for v in terrain.data.vertices]
        min_wz = min(world_zs) if world_zs else 0.0
        check('Terrain Z < 0 (below road)', min_wz < -0.001,
              f'location.z={tz}, world_min_z={min_wz:.4f}')

wall = obj_exists('1WALL0', 'MESH')
if wall:
    has_material_slot(wall)
    check('1WALL0 has vertices', len(wall.data.vertices) > 0,
          f'{len(wall.data.vertices)} verts')
    # Wall should span at least road width + wall thickness in X
    ww, wl = mesh_bounds(wall)
    check('1WALL0 encloses road width',  ww >= road_w, f'{ww:.1f}m vs road {road_w}m')
    check('1WALL0 encloses road length', wl >= road_l, f'{wl:.1f}m vs road {road_l}m')

# ── Cone template ──────────────────────────────────────────────────────────────

cone = obj_exists('AC_POBJECT_MovableCone', 'MESH')
if cone:
    has_material_slot(cone)
    check('AC_POBJECT_MovableCone hide_render', cone.hide_render)

    verts = cone.data.vertices
    if verts:
        zs = [v.co.z for v in verts]
        xs = [abs(v.co.x) for v in verts]
        min_z   = min(zs)
        max_z   = max(zs)
        base_r  = max(xs)
        height  = max_z - min_z

        check('Cone base at Z>=0 (origin at base centre)',
              min_z >= -0.001, f'min_z={min_z:.4f}m')
        check('Cone height in valid range (0.3–1.0m)',
              0.3 < height < 1.0, f'height={height:.4f}m')
        check('Cone base_r >= 0.1397m',
              base_r >= 0.1397 - 0.001, f'base_r={base_r:.4f}m')

# ── AC spawn markers (must be EMPTY) ──────────────────────────────────────────

for mname in ('AC_PIT_0', 'AC_START_0', 'AC_HOTLAP_START_0'):
    obj = obj_exists(mname, 'EMPTY')
    if obj:
        check(f'{mname} rotation.x ~ -pi/2',
              abs(obj.rotation_euler.x - (-math.pi / 2)) < 0.001,
              f'rx={obj.rotation_euler.x:.4f}')
        check(f'{mname} Z >= 1.0',
              obj.location.z >= 1.0,
              f'Z={obj.location.z}')

# ── Timing gate empties ────────────────────────────────────────────────────────

for mname in ('AC_TIME_0_L', 'AC_TIME_0_R', 'AC_TIME_1_L', 'AC_TIME_1_R'):
    obj = obj_exists(mname, 'EMPTY')
    if obj:
        check(f'{mname} hide_render', obj.hide_render)

# ── Materials ─────────────────────────────────────────────────────────────────

for mat_name in ('ROAD', 'Grass', 'ConcreteWall', 'Cone', 'Null'):
    check(f'material {mat_name!r} exists',
          mat_name in bpy.data.materials,
          '' if mat_name in bpy.data.materials else 'not found')

# ── All objects have at least one material slot ────────────────────────────────

no_slot = [o.name for o in bpy.data.objects
           if o.type == 'MESH' and len(o.material_slots) == 0]
check('All MESH objects have material slots', len(no_slot) == 0,
      f'missing slots: {no_slot}' if no_slot else '')

# ── Output JSON ───────────────────────────────────────────────────────────────

print('SCENE_CHECK_RESULTS:' + json.dumps({'results': results}))
