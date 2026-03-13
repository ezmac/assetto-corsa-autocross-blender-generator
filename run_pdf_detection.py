"""
run_pdf_detection.py — Batch cone detection over a list of Solo Nationals PDFs.

Reads a JSON jobs file and runs detect_cones_pdf.run() for each entry.
Outputs JSON (and optional preview PNG) to a configurable directory.

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
  pdf         Path to PDF (relative to CWD or absolute).            [required]
  page        1-indexed page number.                                [required]
  name        Output base name (no extension). Used for .json/.png. [required]
  map         Whether to write a clean map PNG at 72 DPI (1px=1pt). [default: false]
  preview     Whether to write an annotated PNG.                    [default: false]
  snap        Whether to snap pointers to 3-inch increments.        [default: true]
  snap_radius Max anchor-pointer distance for snapping (metres).    [default: 5.0]
  skip        Set true to skip this entry without removing it.      [default: false]

Usage:
  python run_pdf_detection.py --jobs jobs.json [--out-dir generated/solonats]
"""

import sys
import json
import argparse
import traceback
from pathlib import Path

from detect_cones_pdf import run as detect_run
from detect_cones import POINTER_SNAP_ANCHOR_RADIUS_M


def process_job(job, out_dir: Path):
    pdf_path   = job["pdf"]
    page       = int(job["page"])
    name       = job["name"]
    do_map     = bool(job.get("map",     False))
    do_preview = bool(job.get("preview", False))
    do_course  = bool(job.get("course",  False))
    snap       = bool(job.get("snap", True))
    snap_r     = float(job.get("snap_radius", POINTER_SNAP_ANCHOR_RADIUS_M))

    out_json    = out_dir / f"{name}.json"
    map_png     = (out_dir / f"{name}_map.png")     if do_map     else None
    preview_png = (out_dir / f"{name}_preview.png") if do_preview else None
    course_png  = (out_dir / f"{name}_course.png")  if do_course  else None

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
    )

    print(f"  Done: {result['n_standing']} standing, {result['n_pointer']} pointer, "
          f"{result['n_timing_start']} t-start, {result['n_timing_end']} t-end",
          flush=True)
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
