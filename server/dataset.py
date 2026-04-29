"""Torch Dataset for completed periocular images + their class-id masks."""
from __future__ import annotations

from pathlib import Path

import albumentations as A
import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

import config


_COMMON_TRANSFORMS = [
    A.Resize(config.IMAGE_SIZE, config.IMAGE_SIZE, interpolation=1),
    A.Normalize(mean=config.IMAGENET_MEAN, std=config.IMAGENET_STD),
]

TRAIN_AUG = A.Compose([
    A.HorizontalFlip(p=0.5),
    A.RandomBrightnessContrast(brightness_limit=0.15, contrast_limit=0.15, p=0.5),
    A.GaussNoise(std_range=(0.02, 0.06), p=0.2),
    A.Affine(scale=(0.9, 1.1), translate_percent=0.05, rotate=(-10, 10), p=0.5),
    *_COMMON_TRANSFORMS,
])
VAL_AUG = A.Compose(_COMMON_TRANSFORMS)


class PeriocularDataset(Dataset):
    """Pairs of (image, class-id mask) at IMAGE_SIZE × IMAGE_SIZE."""

    def __init__(self, pairs: list[tuple[Path, Path]], augment: bool = True):
        self.pairs = pairs
        self.transform = TRAIN_AUG if augment else VAL_AUG

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, idx: int):
        img_path, mask_path = self.pairs[idx]
        img = np.array(Image.open(img_path).convert("RGB"))
        mask = np.array(Image.open(mask_path))
        if mask.ndim == 3:
            mask = mask[..., 0]
        out = self.transform(image=img, mask=mask)
        x = torch.from_numpy(out["image"].transpose(2, 0, 1)).float()
        y = torch.from_numpy(out["mask"]).long()
        return x, y
