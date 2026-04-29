"""Inspect a directory of class-id PNG masks and propose a v1.0 client config.

Useful when porting a new dataset whose class IDs are unknown: the script
scans the masks, reports the unique IDs, and emits a JSON that can be fed
into ``data/client_config.json`` (or POSTed to ``/config``).

Usage::

    python scripts/derive_palette_from_labels.py path/to/labels/
        [--output proposed_config.json]
        [--names background,iris,sclera,...]   # one per unique class id

If ``--names`` is omitted the script prints stub names (``class_0``, ...).
A reasonable default RGB palette is produced from a 12-step HSV wheel; you
can edit the JSON before submission.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def _scan(directory: Path, sample_limit: int) -> set[int]:
    seen: set[int] = set()
    files = sorted(p for p in directory.iterdir()
                   if p.is_file() and p.suffix.lower() == ".png")
    if sample_limit > 0:
        files = files[:sample_limit]
    if not files:
        raise SystemExit(f"no PNG files found under {directory}")
    for p in files:
        arr = np.array(Image.open(p))
        if arr.ndim == 3:
            arr = arr[..., 0]
        seen.update(int(v) for v in np.unique(arr))
    return seen


def _default_palette(n: int) -> list[list[int]]:
    palette = [[0, 0, 0]]  # background reserved as black
    for i in range(1, n):
        h = ((i - 1) / max(n - 1, 1)) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.85, 0.95)
        palette.append([int(r * 255), int(g * 255), int(b * 255)])
    return palette


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path,
                   help="Directory containing class-id PNG masks")
    p.add_argument("--output", type=Path, default=None,
                   help="Optional JSON path to write the proposed config")
    p.add_argument("--names", default="",
                   help="Comma-separated class names (in id order). "
                        "Length must equal the number of unique class ids.")
    p.add_argument("--sample", type=int, default=0,
                   help="Scan at most this many files (0 = all).")
    args = p.parse_args()

    if not args.directory.is_dir():
        print(f"not a directory: {args.directory}", file=sys.stderr)
        return 1

    ids = sorted(_scan(args.directory, args.sample))
    print(f"found {len(ids)} unique class ids: {ids}")
    if min(ids) != 0 or ids != list(range(min(ids), max(ids) + 1)):
        print("WARNING: class ids are not contiguous from 0; review the dataset",
              file=sys.stderr)

    n = max(ids) + 1
    if args.names:
        names = [s.strip() for s in args.names.split(",")]
        if len(names) != n:
            print(f"--names has {len(names)} entries, expected {n}", file=sys.stderr)
            return 2
    else:
        names = [f"class_{i}" for i in range(n)]
        if names:
            names[0] = "background"

    config_payload = {
        "num_classes": n,
        "class_names": names,
        "palette": _default_palette(n),
    }
    text = json.dumps(config_payload, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
