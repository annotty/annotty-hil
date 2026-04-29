"""5-fold CV training driver for the v1.0 server.

Public API::

    best_metric, version_str = train_model(
        training_pairs=...,
        model_save_path=BEST_MODEL_PATH,
        max_epochs=50,
        num_classes=7,
        status_callback=update_training_status,   # dict -> None
        cancel_event=cancel_event,                # threading.Event
    )

The trainer is **class-agnostic**: ``num_classes`` is supplied at call
time. Loss is ``CrossEntropy + multiclass DiceLoss`` (the upstream binary
recipe collapses to the same formulation when num_classes == 2).
"""
from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Callable, Iterable, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from segmentation_models_pytorch.losses import DiceLoss
from sklearn.model_selection import KFold
from torch.utils.data import DataLoader

import config
from dataset import PeriocularDataset
from model import create_model
from version_manager import bump_version

log = logging.getLogger(__name__)


class TrainingCancelled(Exception):
    """Raised when a cancel_event is observed during training."""


StatusCallback = Callable[[dict], None]


def _emit(callback: Optional[StatusCallback], **kwargs) -> None:
    if callback is None:
        return
    try:
        callback(kwargs)
    except Exception:  # noqa: BLE001
        log.exception("status callback raised")


def _check_cancel(cancel_event: Optional[threading.Event]) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise TrainingCancelled()


def _validate(
    model: torch.nn.Module,
    loader: DataLoader,
    device: torch.device,
    ce: nn.Module,
    dice: nn.Module,
    num_classes: int,
) -> tuple[float, float]:
    model.eval()
    total = 0
    loss_sum = 0.0
    inter = np.zeros(num_classes, dtype=np.float64)
    denom = np.zeros(num_classes, dtype=np.float64)
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            logits = model(x)
            loss = ce(logits, y) + dice(logits, y)
            loss_sum += float(loss.item()) * x.size(0)
            total += x.size(0)
            pred = logits.argmax(dim=1)
            for c in range(num_classes):
                p_c = (pred == c)
                t_c = (y == c)
                inter[c] += float((p_c & t_c).sum().item())
                denom[c] += float(p_c.sum().item() + t_c.sum().item())
    dice_per_class = np.where(denom > 0, 2 * inter / np.maximum(denom, 1), 0.0)
    if num_classes >= 2:
        # mean Dice over foreground classes (skip background class 0)
        fg_dice = float(dice_per_class[1:].mean())
    else:
        fg_dice = float(dice_per_class.mean())
    return loss_sum / max(total, 1), fg_dice


def _train_one_fold(
    fold_idx: int,
    n_folds: int,
    max_epochs: int,
    num_classes: int,
    train_pairs: list[tuple[Path, Path]],
    val_pairs: list[tuple[Path, Path]],
    device: torch.device,
    status_callback: Optional[StatusCallback],
    cancel_event: Optional[threading.Event],
    best_metric_so_far: float,
) -> float:
    train_ds = PeriocularDataset(train_pairs, augment=True)
    val_ds = PeriocularDataset(val_pairs, augment=False)
    tl = DataLoader(
        train_ds, batch_size=config.BATCH_SIZE, shuffle=True,
        num_workers=config.NUM_WORKERS, pin_memory=True,
    )
    vl = DataLoader(
        val_ds, batch_size=config.BATCH_SIZE, shuffle=False,
        num_workers=config.NUM_WORKERS, pin_memory=True,
    )

    model = create_model(num_classes=num_classes).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.LEARNING_RATE,
        weight_decay=config.WEIGHT_DECAY,
    )
    ce = nn.CrossEntropyLoss()
    dice = DiceLoss(mode="multiclass", from_logits=True)

    best_fold_dice = 0.0
    for epoch in range(1, max_epochs + 1):
        _check_cancel(cancel_event)
        model.train()
        for x, y in tl:
            _check_cancel(cancel_event)
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            optimizer.zero_grad()
            logits = model(x)
            loss = ce(logits, y) + dice(logits, y)
            loss.backward()
            optimizer.step()

        _, val_dice = _validate(model, vl, device, ce, dice, num_classes)
        if val_dice > best_fold_dice:
            best_fold_dice = val_dice
            torch.save(
                model.state_dict(),
                config.get_fold_model_path(fold_idx + 1),
            )

        cumulative_epoch = fold_idx * max_epochs + epoch
        _emit(
            status_callback,
            epoch=cumulative_epoch,
            current_fold=fold_idx,
            n_folds=n_folds,
            best_metric=max(best_metric_so_far, best_fold_dice),
        )

    return best_fold_dice


def train_model(
    training_pairs: Iterable[tuple[Path, Path]],
    model_save_path: Path,
    max_epochs: int = config.DEFAULT_MAX_EPOCHS,
    num_classes: int = 2,
    status_callback: Optional[StatusCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[float, str]:
    """Run K-fold CV. Returns ``(mean_fg_dice, version_string)``.

    Raises :class:`TrainingCancelled` if the cancel event is observed.
    """
    pairs = list(training_pairs)
    if len(pairs) < config.MIN_IMAGES_FOR_TRAINING:
        raise ValueError(
            f"need >= {config.MIN_IMAGES_FOR_TRAINING} pairs, have {len(pairs)}"
        )

    n_folds = min(config.N_FOLDS, max(2, len(pairs)))
    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info(
        "training: pairs=%d, folds=%d, max_epochs=%d, num_classes=%d, device=%s",
        len(pairs), n_folds, max_epochs, num_classes, device,
    )

    fold_dices: list[float] = []
    best_metric_so_far = 0.0
    for fold_idx, (tr_idx, va_idx) in enumerate(kf.split(pairs)):
        _check_cancel(cancel_event)
        tr_pairs = [pairs[i] for i in tr_idx]
        va_pairs = [pairs[i] for i in va_idx]
        _emit(
            status_callback,
            current_fold=fold_idx,
            n_folds=n_folds,
        )
        fold_dice = _train_one_fold(
            fold_idx=fold_idx,
            n_folds=n_folds,
            max_epochs=max_epochs,
            num_classes=num_classes,
            train_pairs=tr_pairs,
            val_pairs=va_pairs,
            device=device,
            status_callback=status_callback,
            cancel_event=cancel_event,
            best_metric_so_far=best_metric_so_far,
        )
        fold_dices.append(fold_dice)
        best_metric_so_far = max(best_metric_so_far, fold_dice)

    # Promote the best fold's checkpoint as the canonical best.pt
    best_fold = int(np.argmax(fold_dices))
    src = config.get_fold_model_path(best_fold + 1)
    if src.exists():
        model_save_path.parent.mkdir(parents=True, exist_ok=True)
        model_save_path.write_bytes(src.read_bytes())

    mean_dice = float(np.mean(fold_dices)) if fold_dices else 0.0
    info = bump_version()
    version_str = str(info.get("version", "0"))
    log.info("training done: mean_dice=%.4f, version=%s", mean_dice, version_str)
    return mean_dice, version_str
