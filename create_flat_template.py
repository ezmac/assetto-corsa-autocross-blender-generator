"""
create_flat_template.py — Procedurally build a flat AC autocross track scene in Blender.

Run via:
    blender --background --python create_flat_template.py -- \\
        --name <track_name> [--width 120] [--length 80] --output <project_root>

Creates all required Blender objects, exports FBX, and saves .blend.
Output goes to <project_root>/blender/.
"""

import sys
import os
import math
import argparse
import bpy
import bmesh


# ── Parse args ────────────────────────────────────────────────────────────────

def parse_args():
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []
    p = argparse.ArgumentParser(description='Create flat AC track template in Blender')
    p.add_argument('--name',   required=True,        help='Track name')
    p.add_argument('--width',  type=float, default=120.0, help='Road width in metres (default 120)')
    p.add_argument('--length', type=float, default=80.0,  help='Road length in metres (default 80)')
    p.add_argument('--output',     required=True, help='Project root dir (generated/<name>)')
    p.add_argument('--cone-blend', default=None,  help='Path to .blend containing cone asset (optional)')
    return p.parse_args(argv)


# ── Helpers ───────────────────────────────────────────────────────────────────

def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        if block.users == 0:
            bpy.data.meshes.remove(block)


def make_material(name, color_rgba):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.diffuse_color = color_rgba
    return mat


def assign_material(obj, mat):
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def make_plane(name, width, length, z=0.0, mat=None):
    """Create a flat plane centred at origin with applied scale."""
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = name
    obj.scale = (width, length, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    obj.location.z = z  # set after transform_apply to avoid operator-context quirks
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
    bpy.ops.object.mode_set(mode='OBJECT')
    if mat:
        assign_material(obj, mat)
    return obj


def make_wall(name, road_w, road_l, wall_thickness=0.5, wall_height=2.0, mat=None):
    """Build a closed rectangular wall loop as a single mesh using bmesh."""
    t = wall_thickness
    h = wall_height
    hw = road_w / 2
    hl = road_l / 2

    bm = bmesh.new()

    def add_box(cx, cy, dx, dy):
        """Add a box with centre (cx, cy, h/2), footprint dx × dy, height h."""
        x0, x1 = cx - dx / 2, cx + dx / 2
        y0, y1 = cy - dy / 2, cy + dy / 2
        z0, z1 = 0.0, h
        v = [
            bm.verts.new((x0, y0, z0)), bm.verts.new((x1, y0, z0)),
            bm.verts.new((x1, y1, z0)), bm.verts.new((x0, y1, z0)),
            bm.verts.new((x0, y0, z1)), bm.verts.new((x1, y0, z1)),
            bm.verts.new((x1, y1, z1)), bm.verts.new((x0, y1, z1)),
        ]
        for fi in [(0,1,2,3), (7,6,5,4),
                   (0,1,5,4), (3,2,6,7),
                   (0,3,7,4), (1,2,6,5)]:
            bm.faces.new([v[i] for i in fi])

    # Front wall (+Y edge): spans full width including corner thickness
    add_box(0,            hl + t / 2,  road_w + 2 * t, t)
    # Back wall (-Y edge)
    add_box(0,           -(hl + t / 2), road_w + 2 * t, t)
    # Left wall (-X edge): spans road length only (corners handled above)
    add_box(-(hw + t / 2), 0,           t,              road_l)
    # Right wall (+X edge)
    add_box( (hw + t / 2), 0,           t,              road_l)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    if mat:
        assign_material(obj, mat)
    return obj


def make_cone_template(mat_cone, cone_blend_path=None):
    """Create AC_POBJECT_MovableCone.

    If cone_blend_path points to a valid .blend asset file, the first cone
    object found there is appended and renamed.  Falls back to a procedurally
    generated cone if the file is absent or contains no objects.
    """
    if cone_blend_path and os.path.isfile(cone_blend_path):
        obj = _append_cone_from_blend(cone_blend_path, mat_cone)
        if obj is not None:
            return obj
        print(f"  WARNING: could not append cone from {cone_blend_path!r}, using procedural fallback")

    return _make_procedural_cone(mat_cone)


def _append_cone_from_blend(cone_blend_path, mat_cone):
    """Append the first cone-like object from a blend asset file."""
    with bpy.data.libraries.load(cone_blend_path, link=False) as (src, dst):
        # Prefer objects whose name contains 'Cone', fall back to first object
        candidates = [n for n in src.objects if 'cone' in n.lower()]
        pick = candidates[0] if candidates else (src.objects[0] if src.objects else None)
        if pick is None:
            return None
        dst.objects = [pick]

    obj = dst.objects[0]
    if obj is None:
        return None

    bpy.context.scene.collection.objects.link(obj)
    obj.name      = 'AC_POBJECT_MovableCone'
    obj.data.name = 'AC_POBJECT_MovableCone'
    obj.hide_render = True

    # Replace every material slot that contains 'cone' in its name with our
    # 'Cone' material so the name is consistent for ksEditor.  Other slots
    # (e.g. RubberBlack) are left untouched.
    for slot in obj.material_slots:
        if slot.material and 'cone' in slot.material.name.lower():
            # Remove the imported copy and point to our canonical 'Cone' mat
            old_mat = slot.material
            slot.material = mat_cone
            if old_mat.name != mat_cone.name and old_mat.users == 0:
                bpy.data.materials.remove(old_mat)

    verts = obj.data.vertices
    if verts:
        xs = [abs(v.co.x) for v in verts]
        zs = [v.co.z for v in verts]
        print(f"  AC_POBJECT_MovableCone: appended from {os.path.basename(cone_blend_path)}"
              f"  base_r={max(xs):.4f}  height={max(zs):.4f}  min_z={min(zs):.4f}")
    return obj


def _make_procedural_cone(mat_cone):
    """Fallback: build a simple cone mesh procedurally."""
    bpy.ops.mesh.primitive_cone_add(
        vertices=16, radius1=0.1397, radius2=0, depth=0.4318,
        location=(0, 0, 0)
    )
    obj = bpy.context.active_object
    obj.name      = 'AC_POBJECT_MovableCone'
    obj.data.name = 'AC_POBJECT_MovableCone'

    # primitive_cone_add centres mesh at Z=0 (tip at +depth/2, base at -depth/2).
    # Translate mesh vertices +depth/2 so the base centre sits at local Z=0.
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.transform.translate(value=(0, 0, 0.4318 / 2))
    bpy.ops.object.mode_set(mode='OBJECT')

    obj.hide_render = True
    assign_material(obj, mat_cone)
    print(f"  AC_POBJECT_MovableCone: procedural  base_r=0.1397  height=0.4318  min_z=0.0")
    return obj


def make_empty(name, location, rotation=None):
    """Create an Empty (no geometry) at the given location."""
    if rotation is None:
        rotation = (-math.pi / 2, 0.0, 0.0)
    empty = bpy.data.objects.new(name, None)
    empty.location = location
    empty.rotation_euler = rotation
    empty.hide_render = True
    bpy.context.scene.collection.objects.link(empty)
    return empty


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    name          = args.name
    road_w        = args.width
    road_l        = args.length
    cone_blend    = args.cone_blend

    out_root    = os.path.abspath(args.output)
    blender_dir = os.path.join(out_root, 'blender')
    os.makedirs(blender_dir, exist_ok=True)

    blend_path = os.path.join(blender_dir, f'{name}.blend')
    fbx_path   = os.path.join(blender_dir, f'{name}.fbx')

    print(f"=== create_flat_template: {name}  ({road_w}m x {road_l}m) ===")
    print(f"  Output dir: {blender_dir}")

    # ── Clear default scene ────────────────────────────────────────────────────
    clear_scene()

    # ── Materials ─────────────────────────────────────────────────────────────
    mat_road  = make_material('ROAD',         (0.15, 0.15, 0.15, 1.0))
    mat_grass = make_material('Grass',        (0.13, 0.40, 0.08, 1.0))
    mat_wall  = make_material('ConcreteWall', (0.70, 0.70, 0.70, 1.0))
    mat_cone  = make_material('Cone',         (0.90, 0.35, 0.00, 1.0))
    mat_null = make_material('Null',          (0.00, 0.00, 0.00, 1.0))
    mat_null.use_fake_user = True   # prevent orphan purge on save (Null is never assigned)

    # ── Road plane ────────────────────────────────────────────────────────────
    make_plane('1ROAD0', road_w, road_l, z=0.0, mat=mat_road)
    print(f"  1ROAD0: {road_w}m x {road_l}m")

    # ── Grass plane (road + 50 m padding each side = +100 total) ──────────────
    grass_w = road_w + 100.0
    grass_l = road_l + 100.0
    make_plane('1GRASS0', grass_w, grass_l, z=0.0, mat=mat_grass)
    print(f"  1GRASS0: {grass_w}m x {grass_l}m")

    # ── Terrain (slightly below road to avoid z-fighting) ─────────────────────
    terrain_w = road_w + 200.0
    terrain_l = road_l + 200.0
    make_plane('Terrain', terrain_w, terrain_l, z=-0.05, mat=mat_grass)
    print(f"  Terrain: {terrain_w}m x {terrain_l}m at Z=-0.05")

    # ── Perimeter wall ────────────────────────────────────────────────────────
    make_wall('1WALL0', road_w, road_l, wall_thickness=0.5, wall_height=2.0, mat=mat_wall)
    print(f"  1WALL0: loop around {road_w}m x {road_l}m road")

    # ── Cone template ─────────────────────────────────────────────────────────
    make_cone_template(mat_cone, cone_blend_path=cone_blend)

    # ── AC spawn markers (placeholder at road centre, Z+1.5) ──────────────────
    spawn_rot = (-math.pi / 2, 0.0, 0.0)
    for mname in ('AC_PIT_0', 'AC_START_0', 'AC_HOTLAP_START_0'):
        make_empty(mname, (0.0, 0.0, 1.5), rotation=spawn_rot)
    print(f"  Spawn markers: AC_PIT_0, AC_START_0, AC_HOTLAP_START_0 at (0,0,1.5)")

    # ── Timing gate empties (placeholder positions near road edges) ────────────
    timing_rot = (-math.pi / 2, 0.0, 0.0)
    gate_y = road_l / 2 - 5.0
    make_empty('AC_TIME_0_L', (-5.0,  gate_y, 1.5), rotation=timing_rot)
    make_empty('AC_TIME_0_R', ( 5.0,  gate_y, 1.5), rotation=timing_rot)
    make_empty('AC_TIME_1_L', (-5.0, -gate_y, 1.5), rotation=timing_rot)
    make_empty('AC_TIME_1_R', ( 5.0, -gate_y, 1.5), rotation=timing_rot)
    print(f"  Timing empties: AC_TIME_0/1 L/R at Y=±{gate_y}")

    # ── Export FBX ────────────────────────────────────────────────────────────
    print(f"Exporting FBX: {fbx_path}")
    bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        object_types={'MESH', 'EMPTY'},
        global_scale=1.0,
        apply_scale_options='FBX_SCALE_ALL',
        use_selection=False,
        path_mode='AUTO',
    )

    # ── Save .blend ───────────────────────────────────────────────────────────
    print(f"Saving blend: {blend_path}")
    bpy.ops.wm.save_mainfile(filepath=blend_path)

    print(f"=== Done ===")
    print(f"  Blend: {blend_path}")
    print(f"  FBX:   {fbx_path}")


if __name__ == '__main__':
    main()
