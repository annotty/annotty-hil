"""Inspect a directory of class-id PNG masks and propose the class definition.

Useful when porting a new dataset whose class IDs are unknown: the script
scans the masks, reports the unique IDs, and emits a JSON for
``data/client_config.json`` (the server's class definition, protocol §5.2).

Usage::

    python scripts/derive_palette_from_labels.py path/to/labels/
        [--output proposed_config.json]
        [--names background,iris,sclera,...]   # one per unique class id

If ``--names`` is omitted the script prints stub names (``class_0``, ...).
The default palette is the iPad client palette (protocol §5.2); the client
replaces it via ``POST /config`` anyway.
"""
from __future__ import annotations

import argparse
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


# iPad client palette (protocol §5.2). Background is white.
IPAD_PALETTE = [
    [255, 255, 255], [255, 0, 0], [255, 128, 0], [255, 255, 0], [0, 255, 0],
    [0, 255, 255], [0, 0, 255], [128, 0, 255], [255, 102, 178],
]


def _default_palette(n: int) -> list[list[int]]:
    if n > len(IPAD_PALETTE):
        raise SystemExit(f"{n} classes found; the iPad client supports at most {len(IPAD_PALETTE)}")
    return [list(rgb) for rgb in IPAD_PALETTE[:n]]


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
