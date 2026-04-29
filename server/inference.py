"""Multi-class inference + palette rendering for the v1.0 server.

The public entrypoint is ``run_inference(image_path, model_path, palette,
num_classes)``: it ensembles per-fold checkpoints when available, falls
back to a single best checkpoint, applies the supplied palette, and
returns RGB PNG bytes ready to ship over the wire.

A second helper ``render_class_id_png_to_rgb`` converts an existing
class-id PNG (e.g. a seed/amodal-GT mask) to the same RGB representation
without invoking the model.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms

import config
from model import create_model

log = logging.getLogger(__name__)

_NORMALIZE = transforms.Normalize(mean=config.IMAGENET_MEAN, std=config.IMAGENET_STD)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _preprocess(img: Image.Image) -> tuple[torch.Tensor, tuple[int, int]]:
    orig_w, orig_h = img.size
    img = img.convert("RGB").resize(
        (config.IMAGE_SIZE, config.IMAGE_SIZE), Image.BILINEAR
    )
    arr = np.array(img).astype(np.float32) / 255.0
    t = torch.from_numpy(arr.transpose(2, 0, 1))
    t = _NORMALIZE(t).unsqueeze(0)
    return t, (orig_h, orig_w)


def _load_model(path: Path, num_classes: int, device: torch.device) -> torch.nn.Module:
    sd = torch.load(path, map_location=device, weights_only=False)
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    model = create_model(num_classes=num_classes).to(device)
    model.load_state_dict(sd)
    model.eval()
    return model


def _collect_fold_paths() -> list[Path]:
    paths = [config.get_fold_model_path(f) for f in range(1, config.N_FOLDS + 1)]
    return [p for p in paths if p.exists()]


def _palette_lut(palette: list[list[int]] | list[tuple[int, int, int]]) -> np.ndarray:
    """Build a uint8 LUT of shape (256, 3) from a class-id → RGB palette."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    for idx, rgb in enumerate(palette):
        if idx >= 256:
            break
        lut[idx] = np.asarray(rgb, dtype=np.uint8)
    return lut


def _class_id_to_rgb(class_id: np.ndarray, palette: Iterable) -> np.ndarray:
    return _palette_lut(list(palette))[class_id]


def _png_bytes(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@torch.no_grad()
def run_inference(
    image_path: Path,
    model_path: Path,
    palette: list,
    num_classes: int,
) -> Optional[bytes]:
    """Run model inference on the given image and return an RGB PNG.

    Tries ``fold{1..N_FOLDS}.pt`` first (averaged softmax). Falls back to
    ``model_path`` (typically ``best.pt``). Returns ``None`` if no usable
    checkpoint is found.
    """
    paths = _collect_fold_paths() or ([model_path] if model_path.exists() else [])
    if not paths:
        return None

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    with Image.open(image_path) as raw:
        x, (orig_h, orig_w) = _preprocess(raw)
    x = x.to(device, non_blocking=True)

    probs: Optional[torch.Tensor] = None
    for p in paths:
        model = _load_model(p, num_classes=num_classes, device=device)
        logits = model(x)
        soft = F.softmax(logits, dim=1)
        probs = soft if probs is None else probs + soft
        del model
    probs = probs / len(paths)

    probs = F.interpolate(
        probs, size=(orig_h, orig_w), mode="bilinear", align_corners=False
    )
    class_id = probs.argmax(dim=1)[0].to(torch.uint8).cpu().numpy()

    rgb = _class_id_to_rgb(class_id, palette)
    return _png_bytes(rgb)


def render_class_id_png_to_rgb(
    class_id_path: Path,
    palette: list,
    target_size: Optional[tuple[int, int]] = None,
) -> bytes:
    """Render a class-id PNG with the supplied palette as an RGB PNG.

    ``target_size`` (W, H) is optional; if provided and the source mask
    differs in dimensions, it is upsampled with nearest-neighbour.
    """
    arr = np.array(Image.open(class_id_path))
    if arr.ndim == 3:
        arr = arr[..., 0]
    arr = arr.astype(np.uint8)
    if target_size is not None and (arr.shape[1], arr.shape[0]) != target_size:
        arr = np.array(
            Image.fromarray(arr).resize(target_size, Image.NEAREST)
        )
    rgb = _class_id_to_rgb(arr, palette)
    return _png_bytes(rgb)


def has_any_model() -> bool:
    """Convenience: True if any checkpoint (fold or best) is present."""
    if _collect_fold_paths():
        return True
    return config.BEST_MODEL_PATH.exists()
