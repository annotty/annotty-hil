"""Centralised configuration for the Annotty-HIL v1.0 server (3-pool revision).

Class count, names, and palette are supplied at runtime via ``POST /config``
and persisted to ``data/client_config.json``. Run
``scripts/init_periocular_config.py`` once to bootstrap the periocular
7-class workflow.

Data layout (protocol v1.0, 3 pools)::

    data/
    ├── pending/    {images,labels}/   HITL 前。labels は任意 seed。学習×。
    ├── submitted/  {images,labels}/   HITL 後。再 submit で上書き。学習○。
    └── fixed/      {images,labels}/   read-only 固定データ。学習○。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (v1.0 3-pool layout)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# pending: HITL 前。seed labels は任意。学習には含めない。
PENDING_DIR = DATA_DIR / "pending"
PENDING_IMAGES_DIR = PENDING_DIR / "images"
PENDING_LABELS_DIR = PENDING_DIR / "labels"

# submitted: iPad の PUT /submit で確定したもの。再編集可、学習対象。
SUBMITTED_DIR = DATA_DIR / "submitted"
SUBMITTED_IMAGES_DIR = SUBMITTED_DIR / "images"
SUBMITTED_LABELS_DIR = SUBMITTED_DIR / "labels"

# fixed: 完成済の learning set。HITL 不要、read-only、学習対象。
FIXED_DIR = DATA_DIR / "fixed"
FIXED_IMAGES_DIR = FIXED_DIR / "images"
FIXED_LABELS_DIR = FIXED_DIR / "labels"

MODELS_DIR = DATA_DIR / "models"
PYTORCH_MODEL_DIR = MODELS_DIR / "pytorch"
COREML_MODEL_DIR = MODELS_DIR / "coreml"
BEST_MODEL_PATH = PYTORCH_MODEL_DIR / "best.pt"
COREML_PATH = COREML_MODEL_DIR / "model.mlpackage"
COREML_ZIP_PATH = COREML_MODEL_DIR / "model.mlpackage.zip"

STATIC_DIR = DATA_DIR / "static"
LOG_DIR = DATA_DIR / "logs"
TRAIN_STATUS_PATH = LOG_DIR / "train_status.json"
SERVER_LOG_PATH = LOG_DIR / "server.log"

CLIENT_CONFIG_PATH = DATA_DIR / "client_config.json"

for _d in (
    PENDING_IMAGES_DIR, PENDING_LABELS_DIR,
    SUBMITTED_IMAGES_DIR, SUBMITTED_LABELS_DIR,
    FIXED_IMAGES_DIR, FIXED_LABELS_DIR,
    PYTORCH_MODEL_DIR, COREML_MODEL_DIR, STATIC_DIR, LOG_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Model / training hyperparameters
# ---------------------------------------------------------------------------
ENCODER_NAME = "resnet34"
ENCODER_WEIGHTS = "imagenet"
IN_CHANNELS = 3
IMAGE_SIZE = 512  # square

BATCH_SIZE = 4
DEFAULT_MAX_EPOCHS = 50
LEARNING_RATE = 1e-4
WEIGHT_DECAY = 1e-5
N_FOLDS = 5
NUM_WORKERS = 0  # Windows-friendly (avoid worker process spawn issues)

MIN_IMAGES_FOR_TRAINING = 2

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
SERVER_HOST = os.environ.get("ANNOTTY_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("ANNOTTY_PORT", "8000"))

# Filenames must end with one of these suffixes.
ALLOWED_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
LABEL_SUFFIX = ".png"


def get_fold_model_path(fold: int) -> Path:
    """Per-fold checkpoint path (1-indexed)."""
    return PYTORCH_MODEL_DIR / f"fold{fold}.pt"
