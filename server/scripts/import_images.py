"""Bulk-import periocular_dataset into the server's pending pool.

For each (image, label_amodal) pair under
``periocular_dataset/{images, labels_amodal}/`` this copies::

    image -> server/data/pending/images/{image_filename}
    label -> server/data/pending/labels/{image_stem}.png

Per protocol v1.0 (rev3pool) the label filename is the image stem with
``.png`` suffix regardless of the image's own suffix. The label serves
as a seed mask in the pending pool — used as a fallback by ``/infer``
when no model is trained, but **never as training data**.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

import config  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--images", default=str(ROOT / "periocular_dataset/images"))
    p.add_argument("--labels", default=str(ROOT / "periocular_dataset/labels_amodal"))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    img_src = Path(args.images)
    lbl_src = Path(args.labels)
    img_dst = config.PENDING_IMAGES_DIR
    lbl_dst = config.PENDING_LABELS_DIR

    if not img_src.is_dir():
        print(f"images source not found: {img_src}", file=sys.stderr)
        return 1
    if not lbl_src.is_dir():
        print(f"labels source not found: {lbl_src}", file=sys.stderr)
        return 1

    img_index = {p.stem: p for p in img_src.iterdir()
                 if p.suffix.lower() in config.ALLOWED_IMAGE_SUFFIXES}
    lbl_index = {p.stem: p for p in lbl_src.glob("*.png")}
    common = sorted(set(img_index) & set(lbl_index))
    if args.limit:
        common = common[: args.limit]
    print(
        f"importing {len(common)} pairs "
        f"(images={len(img_index)}, labels={len(lbl_index)})"
    )

    n_copied = n_skipped = 0
    for stem in common:
        ip = img_index[stem]
        lp = lbl_index[stem]
        ip_out = img_dst / ip.name
        # v1.0 (rev3pool): label filename is {stem}.png regardless of image suffix.
        lp_out = lbl_dst / (stem + ".png")
        if ip_out.exists() and lp_out.exists() and not args.overwrite:
            n_skipped += 1
            continue
        shutil.copy2(ip, ip_out)
        shutil.copy2(lp, lp_out)
        n_copied += 1
    print(f"done: copied={n_copied}, skipped={n_skipped}")
    print(f"  images dir: {img_dst}")
    print(f"  seed labels dir: {lbl_dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
