"""
run_pdf_detection.py — Batch cone detection over a list of Solo Nationals PDFs.

Reads a JSON jobs file and runs detect_cones_pdf.run() for each entry.
Outputs files to {out_dir}/solonats_{name}/ subdirectories.

Jobs file format (JSON array):
  [
    {
      "pdf":     "SoloNationals/.../2018 Nationals Courses.pdf",
      "page":    2,
      "name":    "2018_west",
      "preview": true
    },
    ...
  ]

Fields:
  pdf                Path to PDF (relative to CWD or absolute).            [required]
  page               1-indexed page number.                                [required]
  name               Output base name (no extension).                      [required]
  map                Whether to write a clean map PNG at 72 DPI (1px=1pt). [default: false]
  preview            Whether to write an annotated PNG.                    [default: false]
  snap               Whether to snap pointers to 3-inch increments.        [default: false]
  snap_radius        Max anchor-pointer distance for snapping (metres).    [default: 5.0]
  skip               Set true to skip this entry without removing it.      [default: false]
  timing_start_cones List of cone numbers marking the start timing gates.  [optional]
  timing_end_cones   List of cone numbers marking the finish timing gates.  [optional]

Output layout:
  {out_dir}/solonats_{name}/
    {name}.json
    {name}_map.png      (if map: true)
    {name}_preview.png  (if preview: true)
    {name}_course.png   (if course: true)
    {name}_chalk.png    (always)

Usage:
  python run_pdf_detection.py --jobs jobs.json [--out-dir generated/solonats]
"""

import sys
import json
import shutil
import argparse
import traceback
from pathlib import Path

from detect_cones_pdf import run as detect_run
from detect_cones import POINTER_SNAP_ANCHOR_RADIUS_M

CHALK_WIDTH_IN = 5.0   # physical chalk line width written to every output


def process_job(job, out_dir: Path):
    pdf_path   = job["pdf"]
    page       = int(job["page"])
    name       = job["name"]
    do_map     = bool(job.get("map",     False))
    do_preview = bool(job.get("preview", False))
    do_course  = bool(job.get("course",  False))
    snap       = bool(job.get("snap", False))
    snap_r     = float(job.get("snap_radius", POINTER_SNAP_ANCHOR_RADIUS_M))
    timing_start_cones = job.get("timing_start_cones")
    timing_end_cones   = job.get("timing_end_cones")
    invert_gates       = bool(job.get("invert_gates", False))

    job_dir = out_dir / f"solonats_{name}"
    job_dir.mkdir(parents=True, exist_ok=True)

    out_json    = job_dir / f"{name}.json"
    chalk_png   = job_dir / f"{name}_chalk.png"
    map_png     = (job_dir / f"{name}_map.png")     if do_map     else None
    preview_png = (job_dir / f"{name}_preview.png") if do_preview else None
    course_png  = (job_dir / f"{name}_course.png")  if do_course  else None

    print(f"\n{'='*60}", flush=True)
    print(f"  {name}  (page {page})", flush=True)
    print(f"  PDF:      {pdf_path}", flush=True)
    print(f"  snap={snap}  snap_radius={snap_r}m  course={do_course}", flush=True)
    print(f"{'='*60}", flush=True)

    result = detect_run(
        pdf_path=pdf_path,
        page_idx=page,
        out_path=str(out_json),
        map_path=str(map_png) if map_png else None,
        preview_path=str(preview_png) if preview_png else None,
        snap_pointers=snap,
        snap_radius_m=snap_r,
        course_path=str(course_png) if course_png else None,
        timing_start_cones=timing_start_cones,
        timing_end_cones=timing_end_cones,
        invert_gates=invert_gates,
        chalk_path=str(chalk_png),
        chalk_width_in=CHALK_WIDTH_IN,
    )

    print(f"  Done: {result['n_standing']} standing, {result['n_pointer']} pointer, "
          f"{result['n_timing_start']} t-start, {result['n_timing_end']} t-end",
          flush=True)

    # Copy chalk PNG and template DDS textures into the build project's blender/texture/
    build_tex_dir = out_dir.parent / f"solonats_{name}" / "blender" / "texture"
    build_tex_dir.mkdir(parents=True, exist_ok=True)

    if chalk_png.is_file():
        shutil.copy2(chalk_png, build_tex_dir / chalk_png.name)
        print(f"  Chalk PNG → {build_tex_dir / chalk_png.name}", flush=True)

    _template_tex = Path(__file__).parent / "templates" / "rem_gymkhana" / "blender" / "texture"
    _dds_names = ["Cone.dds", "Grass.dds", "ConcreteWall.dds", "NULL.dds", "Reflector.dds",
                  "Black.dds", "MetalBright.dds", "Tree01.dds"]
    for fname in _dds_names:
        src = _template_tex / fname
        dst = build_tex_dir / fname
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
            print(f"  Texture → {fname}", flush=True)

    return result


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--jobs",    required=True, help="Path to JSON jobs file")
    ap.add_argument("--out-dir", default="generated/solonats",
                    help="Directory for output files (default: generated/solonats)")
    ap.add_argument("--only",    nargs="*", metavar="NAME",
                    help="Process only jobs with these names (space-separated)")
    args = ap.parse_args()

    jobs_path = Path(args.jobs)
    if not jobs_path.exists():
        print(f"ERROR: jobs file not found: {jobs_path}", file=sys.stderr)
        sys.exit(1)

    with open(jobs_path) as f:
        jobs = json.load(f)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    only_names = set(args.only) if args.only else None

    total = skipped = errors = 0
    for job in jobs:
        name = job.get("name", "?")

        if job.get("skip"):
            print(f"  Skipping {name} (skip=true)")
            skipped += 1
            continue

        if only_names and name not in only_names:
            skipped += 1
            continue

        total += 1
        try:
            process_job(job, out_dir)
        except Exception as e:
            print(f"\nERROR processing {name}: {e}", file=sys.stderr)
            traceback.print_exc()
            errors += 1

    print(f"\nDone. {total} processed, {skipped} skipped, {errors} errors.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
