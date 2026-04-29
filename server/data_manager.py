"""File-system data access layer — protocol v1.0 (3-pool revision).

Layout::

    data/
    ├── pending/     {images,labels}/   HITL 前。labels は任意 seed。学習×。
    ├── submitted/   {images,labels}/   HITL 後。再 submit で上書き。学習○。
    └── fixed/       {images,labels}/   read-only 固定データ。学習○。

Naming convention (upstream §9): label filename is the image **stem** with
suffix ``.png`` regardless of the image suffix. So ``foo.jpg`` is paired
with ``foo.png`` in the same pool's ``labels/`` directory.

Invariants:
  * ``fixed/`` is never written via the API.
  * ``pending/labels/`` seeds are excluded from training.
  * ``PUT /submit/{id}`` branches by source pool: pending images are
    physically moved to ``submitted/`` (seed dropped); submitted labels
    are overwritten in place; fixed is rejected with ``PoolReadOnlyError``.
"""
from __future__ import annotations

import logging
import random
import re
import shutil
from dataclasses import dataclass, asdict
from io import BytesIO
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

import config

log = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\-]+\.(png|jpg|jpeg)$", re.IGNORECASE)


def _safe_name(name: str) -> bool:
    return (
        bool(_FILENAME_RE.match(name))
        and ".." not in name
        and "/" not in name
        and "\\" not in name
    )


def _label_filename(image_id: str) -> str:
    """Return the canonical label filename for a given image_id.

    Per protocol v1.0 (rev3pool) the label is always ``{stem}.png``
    regardless of the image suffix.
    """
    return Path(image_id).stem + config.LABEL_SUFFIX


def _rgb_to_class_id(rgb: np.ndarray, palette: list[list[int]]) -> np.ndarray:
    """Reverse-lookup an RGB palette PNG to a class-id array.

    Each pixel must exactly match one ``palette[i]`` entry. Pixels that
    don't match any palette entry raise ``ValueError`` listing the first
    offender — this catches anti-aliased / interpolated masks that
    introduced intermediate colours (forbidden by §5.1).
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected (H,W,3), got shape {rgb.shape}")
    h, w, _ = rgb.shape
    out = np.full((h, w), 255, dtype=np.uint8)  # 255 = unmapped sentinel
    for idx, color in enumerate(palette):
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        match = (rgb[..., 0] == r) & (rgb[..., 1] == g) & (rgb[..., 2] == b)
        out[match] = idx
    bad = out == 255
    if bad.any():
        ys, xs = np.nonzero(bad)
        y, x = int(ys[0]), int(xs[0])
        bad_rgb = tuple(int(v) for v in rgb[y, x])
        raise ValueError(
            f"mask contains pixel {bad_rgb} at ({x},{y}) that is not in the palette "
            "(client must use nearest-neighbour resize and avoid alpha blending)"
        )
    return out


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ImageNotFoundError(LookupError):
    """Raised when an image_id is not present in any pool."""


class PoolReadOnlyError(PermissionError):
    """Raised on attempted writes to the fixed pool."""


# ---------------------------------------------------------------------------
# Image meta
# ---------------------------------------------------------------------------
@dataclass
class ImageMeta:
    image_id: str
    pool: str           # "pending" | "submitted" | "fixed"
    has_seed: bool
    has_annotation: bool
    bytes: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# DataManager
# ---------------------------------------------------------------------------
class DataManager:
    """Filesystem CRUD across the pending / submitted / fixed pools."""

    POOLS: tuple[str, ...] = ("pending", "submitted", "fixed")

    def __init__(self) -> None:
        self._pool_paths: dict[str, tuple[Path, Path]] = {
            "pending":   (config.PENDING_IMAGES_DIR,   config.PENDING_LABELS_DIR),
            "submitted": (config.SUBMITTED_IMAGES_DIR, config.SUBMITTED_LABELS_DIR),
            "fixed":     (config.FIXED_IMAGES_DIR,     config.FIXED_LABELS_DIR),
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self, image_id: str) -> None:
        if not _safe_name(image_id):
            raise ValueError(f"unsafe image_id: {image_id!r}")

    def _pool_dirs(self, pool: str) -> tuple[Path, Path]:
        try:
            return self._pool_paths[pool]
        except KeyError as e:
            raise ValueError(f"unknown pool: {pool!r}") from e

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def _list_dir(self, directory: Path, suffixes: tuple[str, ...]) -> list[str]:
        if not directory.is_dir():
            return []
        return sorted(
            p.name for p in directory.iterdir()
            if p.is_file()
            and p.suffix.lower() in suffixes
            and not p.name.startswith(".")
        )

    def list_pool_images(self, pool: str) -> list[str]:
        images_dir, _ = self._pool_dirs(pool)
        return self._list_dir(images_dir, config.ALLOWED_IMAGE_SUFFIXES)

    # ------------------------------------------------------------------
    # Path resolvers
    # ------------------------------------------------------------------
    def find_pool(self, image_id: str) -> Optional[str]:
        """Search submitted → fixed → pending and return the pool name."""
        self.validate(image_id)
        for pool in ("submitted", "fixed", "pending"):
            images_dir, _ = self._pool_dirs(pool)
            if (images_dir / image_id).exists():
                return pool
        return None

    def image_path(self, image_id: str) -> Path:
        pool = self.find_pool(image_id)
        if pool is None:
            raise FileNotFoundError(image_id)
        images_dir, _ = self._pool_dirs(pool)
        return images_dir / image_id

    def get_image_path(self, image_id: str) -> Optional[Path]:
        try:
            return self.image_path(image_id)
        except FileNotFoundError:
            return None

    def get_label_path(self, image_id: str) -> Optional[Path]:
        """Return the canonical label path, searching submitted → fixed → pending."""
        self.validate(image_id)
        label_name = _label_filename(image_id)
        for pool in ("submitted", "fixed", "pending"):
            _, labels_dir = self._pool_dirs(pool)
            p = labels_dir / label_name
            if p.exists():
                return p
        return None

    def seed_label_path(self, image_id: str) -> Optional[Path]:
        """Return the pending-pool seed label path if it exists, else None.

        Used by the fork's /infer fallback when the trained model is absent.
        """
        self.validate(image_id)
        p = config.PENDING_LABELS_DIR / _label_filename(image_id)
        return p if p.exists() else None

    # ------------------------------------------------------------------
    # Flags
    # ------------------------------------------------------------------
    def has_seed(self, image_id: str) -> bool:
        """True iff a seed label exists in pending/labels/ for this image_id."""
        self.validate(image_id)
        return (config.PENDING_LABELS_DIR / _label_filename(image_id)).exists()

    def has_annotation(self, image_id: str) -> bool:
        """True iff a finalised label exists in submitted/ or fixed/."""
        self.validate(image_id)
        label_name = _label_filename(image_id)
        return (
            (config.SUBMITTED_LABELS_DIR / label_name).exists()
            or (config.FIXED_LABELS_DIR / label_name).exists()
        )

    # ------------------------------------------------------------------
    # Image metadata
    # ------------------------------------------------------------------
    def get_image_meta(self, image_id: str) -> ImageMeta:
        pool = self.find_pool(image_id)
        if pool is None:
            raise FileNotFoundError(image_id)
        img_path = self.image_path(image_id)
        try:
            with Image.open(img_path) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            w, h = 0, 0
        return ImageMeta(
            image_id=image_id,
            pool=pool,
            has_seed=self.has_seed(image_id) if pool == "pending" else False,
            has_annotation=self.has_annotation(image_id),
            bytes=img_path.stat().st_size,
            width=w,
            height=h,
        )

    # ------------------------------------------------------------------
    # Active learning
    # ------------------------------------------------------------------
    def get_next_pending(self, strategy: str = "random") -> Optional[str]:
        """Return the next pending image_id, or None if pending is empty."""
        names = self.list_pool_images("pending")
        if not names:
            return None
        if strategy == "sequential":
            return names[0]
        if strategy == "random":
            return random.choice(names)
        raise ValueError(f"unknown strategy: {strategy!r}")

    # ------------------------------------------------------------------
    # Training pairs (submitted ∪ fixed only — pending excluded)
    # ------------------------------------------------------------------
    def get_all_training_pairs(self) -> list[tuple[Path, Path]]:
        pairs: list[tuple[Path, Path]] = []
        for pool in ("submitted", "fixed"):
            images_dir, labels_dir = self._pool_dirs(pool)
            for name in self._list_dir(images_dir, config.ALLOWED_IMAGE_SUFFIXES):
                lbl = labels_dir / _label_filename(name)
                if lbl.exists():
                    pairs.append((images_dir / name, lbl))
        log.info("training pairs: total=%d (submitted ∪ fixed)", len(pairs))
        return pairs

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> dict:
        return {
            "pending":          len(self.list_pool_images("pending")),
            "submitted":        len(self.list_pool_images("submitted")),
            "fixed":            len(self.list_pool_images("fixed")),
            "submitted_labels": len(self._list_dir(config.SUBMITTED_LABELS_DIR, (config.LABEL_SUFFIX,))),
            "fixed_labels":     len(self._list_dir(config.FIXED_LABELS_DIR, (config.LABEL_SUFFIX,))),
            "pending_seeds":    len(self._list_dir(config.PENDING_LABELS_DIR, (config.LABEL_SUFFIX,))),
        }

    # ------------------------------------------------------------------
    # Submit (PUT /submit/{id})
    # ------------------------------------------------------------------
    def submit(
        self,
        image_id: str,
        content: bytes,
        num_classes: int,
        palette: Optional[list[list[int]]] = None,
    ) -> tuple[str, str]:
        """Validate the mask payload and persist it to submitted/.

        Accepted wire formats (per protocol §5.1, plus fork legacy):
          * RGB PNG with palette colours — reverse-looked up against
            ``palette`` to recover class IDs (this is the v1.0 wire format).
          * Single-channel (mode=L) PNG with class IDs — legacy.
          * 3-channel PNG with all channels equal — legacy class-id PNG.

        Behaviour by source pool:
          * pending   → physically move image to submitted/, drop seed,
                        write mask. Returns ("saved", "submitted").
          * submitted → overwrite submitted/labels/ in place (re-submit).
                        Returns ("updated", "submitted").
          * fixed     → raises PoolReadOnlyError.
          * absent    → raises ImageNotFoundError.

        Raises ``ValueError`` on bad payload (empty body, undecodable PNG,
        RGBA, RGB whose colours don't match the palette, or class id
        ≥ ``num_classes``).
        """
        self.validate(image_id)
        if not content:
            raise ValueError("empty body")

        # Decode + sanity-check.
        try:
            with BytesIO(content) as buf:
                img = Image.open(buf)
                img.load()
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"mask decode failed: {e}") from e

        arr = np.array(img)
        if arr.ndim == 3:
            if arr.shape[2] == 4:
                raise ValueError("mask must not have an alpha channel (RGBA)")
            if arr.shape[2] >= 3 and np.array_equal(arr[..., 0], arr[..., 1]) \
               and np.array_equal(arr[..., 1], arr[..., 2]):
                # Legacy form: 3 channels all equal — treat as single class-id channel.
                arr = arr[..., 0]
            elif arr.shape[2] == 3:
                # v1.0 wire form: RGB palette PNG. Reverse-lookup colours.
                if palette is None:
                    raise ValueError(
                        "RGB mask received but no palette configured "
                        "(client must POST /config first)"
                    )
                arr = _rgb_to_class_id(arr, palette)
            else:
                raise ValueError(
                    f"unsupported mask channel count: {arr.shape[2]}"
                )
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8)
        if arr.size and arr.max() >= num_classes:
            raise ValueError(
                f"mask contains class id {int(arr.max())} >= num_classes={num_classes}"
            )

        # Locate source pool.
        pool = self.find_pool(image_id)
        if pool is None:
            raise ImageNotFoundError(f"image '{image_id}' not found in any pool")
        if pool == "fixed":
            raise PoolReadOnlyError("fixed pool is read-only")

        label_name = _label_filename(image_id)
        target_label = config.SUBMITTED_LABELS_DIR / label_name

        if pool == "pending":
            src_image = config.PENDING_IMAGES_DIR / image_id
            dst_image = config.SUBMITTED_IMAGES_DIR / image_id
            shutil.move(str(src_image), str(dst_image))

            seed = config.PENDING_LABELS_DIR / label_name
            if seed.exists():
                seed.unlink()

            Image.fromarray(arr).save(target_label, format="PNG")
            log.info("submitted (new): %s (%d bytes)", image_id, target_label.stat().st_size)
            return ("saved", "submitted")

        # pool == "submitted": label-only overwrite.
        Image.fromarray(arr).save(target_label, format="PNG")
        log.info("submitted (update): %s (%d bytes)", image_id, target_label.stat().st_size)
        return ("updated", "submitted")
