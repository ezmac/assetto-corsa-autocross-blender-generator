"""
new_project.py — Create a new track project from the seneca_runway template.

Usage:
    python new_project.py <track_name>

Example:
    python new_project.py seneca_gp_21_v3

What it does:
    1. Copies the full template folder to generated/<track_name>/
    2. Renames seneca_runway.blend → <track_name>.blend
    3. Updates ui/ui_track.json "name" field with human-readable track name
       (underscores replaced with spaces, title-cased)
"""

import sys
import os
import shutil
import re

_HERE         = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR  = os.path.join(_HERE, 'templates', 'seneca_runway')
GENERATED_DIR = os.path.join(_HERE, 'generated')
TEMPLATE_BLEND = "seneca_runway.blend"

def main():
    if len(sys.argv) < 2:
        print("Usage: python new_project.py <track_name>")
        sys.exit(1)

    track_name = sys.argv[1].strip()
    dest_dir   = os.path.join(GENERATED_DIR, track_name)

    # ── Check destination doesn't already exist ───────────────────────────────
    if os.path.exists(dest_dir):
        print(f"ERROR: Destination already exists: {dest_dir}")
        sys.exit(1)

    # ── Copy template folder ──────────────────────────────────────────────────
    print(f"Copying template to {dest_dir} ...")
    shutil.copytree(TEMPLATE_DIR, dest_dir)

    # ── Rename .blend file ────────────────────────────────────────────────────
    src_blend  = os.path.join(dest_dir, TEMPLATE_BLEND)
    dest_blend = os.path.join(dest_dir, f"{track_name}.blend")
    os.rename(src_blend, dest_blend)
    print(f"Renamed blend: {TEMPLATE_BLEND} -> {track_name}.blend")

    # Also remove the .blend1 backup if present
    blend1 = os.path.join(dest_dir, TEMPLATE_BLEND.replace(".blend", ".blend1"))
    if os.path.exists(blend1):
        os.remove(blend1)

    # ── Rename .kn5 and .fbx file(s) matching the template name ──────────────
    template_stem = os.path.splitext(TEMPLATE_BLEND)[0]  # "seneca_runway"
    for root, _dirs, files in os.walk(dest_dir):
        for f in files:
            if f == f"{template_stem}.kn5":
                old_path = os.path.join(root, f)
                new_path = os.path.join(root, f"{track_name}.kn5")
                os.rename(old_path, new_path)
                print(f"Renamed KN5: {os.path.relpath(old_path, dest_dir)} -> {track_name}.kn5")
            elif f.startswith(template_stem) and f.lower().endswith('.fbx'):
                suffix   = f[len(template_stem):]        # e.g. ".fbx" or "_TREES.fbx"
                old_path = os.path.join(root, f)
                new_path = os.path.join(root, f"{track_name}{suffix}")
                os.rename(old_path, new_path)
                print(f"Renamed FBX: {os.path.relpath(old_path, dest_dir)} -> {track_name}{suffix}")

    # ── Update ui/ui_track.json ───────────────────────────────────────────────
    json_path    = os.path.join(dest_dir, "ui", "ui_track.json")
    human_name   = track_name.replace("_", " ").title()

    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Replace the "name" value — handles trailing commas in AC's loose JSON
        updated = re.sub(
            r'("name"\s*:\s*)"[^"]*"',
            rf'\1"{human_name}"',
            content
        )

        with open(json_path, "w", encoding="utf-8") as f:
            f.write(updated)

        print(f"Updated ui_track.json name: \"{human_name}\"")
    else:
        print(f"WARNING: ui_track.json not found at {json_path}")

    print(f"\nDone. Project ready at:\n  {dest_dir}")
    print(f"Open in Blender:\n  {dest_blend}")

if __name__ == "__main__":
    main()
