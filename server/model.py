"""U-Net (segmentation_models_pytorch) factory for the v1.0 server.

Class count is supplied at call time so the same module can serve any
configuration POSTed via ``/config``.
"""
from __future__ import annotations

import segmentation_models_pytorch as smp

from config import ENCODER_NAME, ENCODER_WEIGHTS, IN_CHANNELS


def create_model(num_classes: int) -> smp.Unet:
    """Return a U-Net producing raw logits with ``num_classes`` channels."""
    if num_classes < 1:
        raise ValueError(f"num_classes must be >= 1, got {num_classes}")
    return smp.Unet(
        encoder_name=ENCODER_NAME,
        encoder_weights=ENCODER_WEIGHTS,
        in_channels=IN_CHANNELS,
        classes=num_classes,
        activation=None,
    )
