#!/usr/bin/env python3
"""
Convert between the manually-corrected editor coordinate system and the
python-parser (PDF) coordinate system.

Editor (manually_corrected) uses:
    transform: { type: scale, scale: 0.3048, ox: 0, oy: 0 }

Parser (generated) uses:
    transform: { type: scale, scale: 0.3048, ox: <value>, oy: <value>, ... }

The relationship:
    x_parser = x_editor + ox
    y_parser = y_editor + oy

The ox/oy stored in the parser file's transform are approximate; use --detect-from
with the matching counterpart file to measure the exact offset from cone positions.
"""

import json
import statistics
from pathlib import Path


def detect_offsets(editor_path: Path, parser_path: Path) -> tuple[float, float]:
    """Measure ox/oy from matching standing cone positions in both files."""
    editor = json.loads(editor_path.read_text())
    parser = json.loads(parser_path.read_text())

    e_cones = editor.get("standing", [])
    p_cones = parser.get("standing", [])
    n = min(len(e_cones), len(p_cones))
    if n == 0:
        raise ValueError("No standing cones found in one or both files")

    dxs = [p_cones[i]["bx"] - e_cones[i]["bx"] for i in range(n)]
    dys = [p_cones[i]["by"] - e_cones[i]["by"] for i in range(n)]

    ox = statistics.mean(dxs)
    oy = statistics.mean(dys)

    spread_x = max(dxs) - min(dxs)
    spread_y = max(dys) - min(dys)
    if spread_x > 0.01 or spread_y > 0.01:
        print(
            f"WARNING: offsets are not consistent (spread dx={spread_x:.4f}, dy={spread_y:.4f}). "
            "Files may not be aligned cone-for-cone."
        )
    else:
        print(f"Detected offsets: ox={ox:.6f}  oy={oy:.6f}  (from {n} cones, spread<0.01m)")

    return ox, oy


def editor_to_parser(x: float, y: float, ox: float, oy: float) -> tuple[float, float]:
    return x + ox, y + oy


def parser_to_editor(x: float, y: float, ox: float, oy: float) -> tuple[float, float]:
    return x - ox, y - oy


def convert_cone_list(cones: list[dict], ox: float, oy: float, direction: str) -> list[dict]:
    fn = editor_to_parser if direction == "to_parser" else parser_to_editor
    result = []
    for cone in cones:
        c = dict(cone)
        c["bx"], c["by"] = fn(cone["bx"], cone["by"], ox, oy)
        result.append(c)
    return result


def convert_file(src_path: Path, dst_path: Path, direction: str, ox: float, oy: float) -> None:
    data = json.loads(src_path.read_text())

    for key in ("standing", "pointers", "timing_start", "timing_end", "gcp"):
        if key in data and isinstance(data[key], list):
            data[key] = convert_cone_list(data[key], ox, oy, direction)

    if "bounds" in data:
        b = data["bounds"]
        fn = editor_to_parser if direction == "to_parser" else parser_to_editor
        xmin, ymin = fn(b["xmin"], b["ymin"], ox, oy)
        xmax, ymax = fn(b["xmax"], b["ymax"], ox, oy)
        data["bounds"] = {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}

    dst_path.write_text(json.dumps(data, indent=2))
    print(f"Wrote {dst_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert cone coordinates between editor and parser systems."
    )
    parser.add_argument("src", help="Source JSON file")
    parser.add_argument("dst", help="Destination JSON file")
    parser.add_argument(
        "--direction",
        choices=["to_parser", "to_editor"],
        required=True,
        help=(
            "to_parser: editor (manually_corrected) → parser (generated); "
            "to_editor: parser (generated) → editor (manually_corrected)"
        ),
    )
    parser.add_argument(
        "--detect-from",
        metavar="COUNTERPART",
        help=(
            "Auto-detect ox/oy by comparing src against this matching file "
            "(editor file when --direction=to_parser; parser file when --direction=to_editor). "
            "Overrides --ox/--oy."
        ),
    )
    parser.add_argument(
        "--ox",
        type=float,
        default=None,
        help="x offset from editor to parser coords",
    )
    parser.add_argument(
        "--oy",
        type=float,
        default=None,
        help="y offset from editor to parser coords",
    )
    args = parser.parse_args()

    if args.detect_from:
        src = Path(args.src)
        ref = Path(args.detect_from)
        if args.direction == "to_parser":
            editor_path, parser_path = src, ref
        else:
            editor_path, parser_path = ref, src
        ox, oy = detect_offsets(editor_path, parser_path)
    elif args.ox is not None and args.oy is not None:
        ox, oy = args.ox, args.oy
    else:
        parser.error("Provide either --detect-from <counterpart> or both --ox and --oy")

    convert_file(Path(args.src), Path(args.dst), args.direction, ox, oy)


if __name__ == "__main__":
    main()
