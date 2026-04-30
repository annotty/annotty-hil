"""Pre-render class-id label PNGs at the iPad client's internal mask resolution.

Why this is needed
------------------
The iPad client (`AnnottyHIL/Models/InternalMask.swift`) computes an
internal mask buffer at ``min(2.0, 4096 / maxEdge)`` × the image
dimensions and scales the server-supplied mask up to that resolution
with **nearest-neighbor** interpolation
(`AnnottyHIL/Services/FileManager/AnnotationLoader.swift`). Nearest
upsampling preserves stair geometry: a 1-pixel staircase on a 1× source
becomes a 2-pixel block staircase on the iPad's 2× internal buffer.
Visually the edge looks chunky.

There is no smoothing-friendly interpolation the iPad can use on a raw
class-id array — bilinear/bicubic would produce intermediate values
(non-existent class IDs). The fix has to live on the server: pre-render
each label at iPad's target resolution and use a class-aware smoothing
method.

Method
------
1. Determine the per-image target dimension via the same formula iPad
   uses: ``scale = min(2.0, 4096 / max(image_w, image_h))``.
2. NEAREST-upscale the label to that target.
3. For each class, smooth the binary indicator with a Gaussian filter.
4. Re-classify each pixel by argmax across smoothed class probabilities.
5. Save back as ``mode="L"`` PNG (class-id PNG).

The script is **idempotent**: a label whose dimensions already match the
target is skipped.

Usage
-----
::

    # default: data/pending/labels & data/pending/images
    python scripts/smooth_labels.py

    # explicit dirs and tuning
    python scripts/smooth_labels.py \\
        --labels-dir data/pending/labels \\
        --images-dir data/pending/images \\
        --sigma 1.5 \\
        --max-mask-edge 4096 \\
        --max-scale 2.0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

import config  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    p.add_argument("--labels-dir", type=Path,
                   default=config.PENDING_LABELS_DIR,
                   help="directory containing class-id label PNGs to rewrite "
                        "in place (default: pending/labels/).")
    p.add_argument("--images-dir", type=Path,
                   default=config.PENDING_IMAGES_DIR,
                   help="directory containing the matching images "
                        "(default: pending/images/). The label's stem must "
                        "match the image's stem.")
    p.add_argument("--sigma", type=float, default=1.5,
                   help="Gaussian sigma for per-class smoothing (default: 1.5).")
    p.add_argument("--max-scale", type=float, default=2.0,
                   help="Maximum upscale factor (default: 2.0). Match iPad's "
                        "InternalMask.calculateDimensions().")
    p.add_argument("--max-mask-edge", type=int, default=4096,
                   help="Cap on max(mask_w, mask_h) (default: 4096). Match "
                        "iPad's InternalMask.maxDimension.")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N labels (0 = all). Useful for "
                        "smoke-testing.")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change without writing.")
    return p.parse_args()


def target_dims(image_w: int, image_h: int, max_scale: float, max_edge: int) -> tuple[int, int, float]:
    """Mirror of iPad ``InternalMask.calculateDimensions``."""
    max_e = max(image_w, image_h)
    scale = min(max_scale, max_edge / max_e)
    return int(image_w * scale), int(image_h * scale), scale


def smooth_class_id(arr: np.ndarray, num_classes: int, sigma: float) -> np.ndarray:
    """Per-class Gaussian + argmax smoothing on a class-id mask.

    arr is expected to already be at the desired output resolution (we do
    the NEAREST upscale outside, so ``arr.shape`` == target shape).
    """
    H, W = arr.shape
    probs = np.zeros((num_classes, H, W), dtype=np.float32)
    for c in range(num_classes):
        binary = (arr == c).astype(np.float32)
        probs[c] = ndimage.gaussian_filter(binary, sigma=sigma)
    return probs.argmax(axis=0).astype(np.uint8)


def process_one(
    label_path: Path,
    image_path: Path,
    sigma: float,
    max_scale: float,
    max_edge: int,
    dry_run: bool,
) -> tuple[str, dict]:
    """Returns (status, info) where status ∈ {written, skipped, error}."""
    info: dict = {}
    try:
        with Image.open(image_path) as im:
            image_w, image_h = im.size
        info["image_size"] = (image_w, image_h)

        tw, th, scale = target_dims(image_w, image_h, max_scale, max_edge)
        info["target"] = (tw, th)
        info["scale"] = scale

        with Image.open(label_path) as lim:
            cur_w, cur_h = lim.size
            label_arr = np.array(lim)
        if label_arr.ndim == 3:
            label_arr = label_arr[..., 0]
        label_arr = label_arr.astype(np.uint8)
        info["current"] = (cur_w, cur_h)
        info["classes"] = sorted(int(v) for v in np.unique(label_arr))

        # Idempotency: if already at target, skip.
        if (cur_w, cur_h) == (tw, th):
            return "skipped", info

        # Step 1: NEAREST-upscale to target.
        upsampled = np.array(
            Image.fromarray(label_arr).resize((tw, th), Image.NEAREST)
        )
        # Step 2-3: per-class Gaussian + argmax.
        num_classes = max(info["classes"]) + 1
        smoothed = smooth_class_id(upsampled, num_classes, sigma)

        if dry_run:
            return "would_write", info

        Image.fromarray(smoothed, mode="L").save(label_path)
        return "written", info
    except Exception as e:  # noqa: BLE001
        info["error"] = repr(e)
        return "error", info


def main() -> int:
    args = parse_args()
    labels_dir = Path(args.labels_dir)
    images_dir = Path(args.images_dir)
    if not labels_dir.is_dir():
        print(f"labels dir not found: {labels_dir}", file=sys.stderr)
        return 1
    if not images_dir.is_dir():
        print(f"images dir not found: {images_dir}", file=sys.stderr)
        return 1

    image_index: dict[str, Path] = {}
    for p in images_dir.iterdir():
        if p.is_file() and p.suffix.lower() in config.ALLOWED_IMAGE_SUFFIXES:
            image_index[p.stem] = p

    label_paths = sorted(labels_dir.glob("*.png"))
    if args.limit:
        label_paths = label_paths[: args.limit]

    print(f"smoothing {len(label_paths)} labels in {labels_dir}")
    print(f"  matching images dir: {images_dir} ({len(image_index)} images)")
    print(f"  sigma={args.sigma}, max_scale={args.max_scale}, max_edge={args.max_mask_edge}")
    if args.dry_run:
        print("  DRY RUN -- no files written")

    n_written = n_skipped = n_orphan = n_error = 0
    for label_path in label_paths:
        stem = label_path.stem
        image_path = image_index.get(stem)
        if image_path is None:
            n_orphan += 1
            print(f"  ORPHAN (no matching image): {label_path.name}")
            continue
        status, info = process_one(
            label_path, image_path,
            sigma=args.sigma,
            max_scale=args.max_scale,
            max_edge=args.max_mask_edge,
            dry_run=args.dry_run,
        )
        if status == "written" or status == "would_write":
            n_written += 1
            if n_written <= 3 or n_written % 500 == 0:
                print(f"  [{n_written}] {label_path.name}: "
                      f"{info['current']} -> {info['target']} (scale={info['scale']:.2f})")
        elif status == "skipped":
            n_skipped += 1
        elif status == "error":
            n_error += 1
            print(f"  ERROR {label_path.name}: {info.get('error')}")

    print(
        f"done: {'would_write' if args.dry_run else 'written'}={n_written}, "
        f"skipped={n_skipped} (already at target), "
        f"orphan={n_orphan}, error={n_error}"
    )
    return 0 if n_error == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
