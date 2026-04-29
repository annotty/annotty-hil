"""Annotty HIL Server — protocol v1.0 reference implementation.

The server is **class-agnostic**: the iPad client (or a setup script)
posts the palette / class_names / num_classes via ``POST /config``. The
configuration is persisted to ``data/client_config.json`` so it survives
restarts. Run ``scripts/init_periocular_config.py`` once to seed the
periocular 7-class workflow.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from datetime import datetime
from typing import Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel

import config
from data_manager import (
    DataManager,
    ImageNotFoundError,
    PoolReadOnlyError,
)
from inference import render_class_id_png_to_rgb, run_inference
from trainer import TrainingCancelled, train_model
from version_manager import bump_version, latest_headers, model_info


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "1.0"
SERVER_NAME = "Annotty HIL Server"
DEFAULT_METRIC_NAME = "dice"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
config.LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.SERVER_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("annotty.server")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title=SERVER_NAME, version=PROTOCOL_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Model-Version",
        "X-Model-Md5",
        "X-Model-Updated-At",
        "X-Model-Source",
    ],
)

dm = DataManager()


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
client_config: dict = {
    "palette": None,
    "class_names": None,
    "num_classes": None,
}
config_lock = threading.Lock()


def _initial_training_status() -> dict:
    return {
        "state": "idle",
        "epoch": None,
        "max_epochs": None,
        "best_metric": None,
        "metric_name": DEFAULT_METRIC_NAME,
        "current_fold": None,
        "n_folds": None,
        "started_at": None,
        "completed_at": None,
        "version": None,
        "error": None,
    }


training_status: dict = _initial_training_status()
training_lock = threading.Lock()
training_cancel_event = threading.Event()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ClientConfig(BaseModel):
    palette: list[list[int]]
    class_names: list[str]
    num_classes: int


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def load_client_config() -> Optional[dict]:
    if not config.CLIENT_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(config.CLIENT_CONFIG_PATH.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and isinstance(data.get("palette"), list)
            and isinstance(data.get("class_names"), list)
            and isinstance(data.get("num_classes"), int)
        ):
            return {
                "palette": [list(rgb) for rgb in data["palette"]],
                "class_names": list(data["class_names"]),
                "num_classes": int(data["num_classes"]),
            }
    except Exception:  # noqa: BLE001
        logger.exception("failed to load %s", config.CLIENT_CONFIG_PATH)
    return None


def save_client_config(cfg: dict) -> None:
    payload = {
        "palette": cfg["palette"],
        "class_names": cfg["class_names"],
        "num_classes": cfg["num_classes"],
    }
    config.CLIENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CLIENT_CONFIG_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def validate_image_id(image_id: str) -> None:
    try:
        dm.validate(image_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid image_id")


def require_palette() -> list[list[int]]:
    with config_lock:
        palette = client_config["palette"]
    if palette is None:
        raise HTTPException(
            status_code=503,
            detail="palette not configured; client must POST /config first",
        )
    return palette


def require_num_classes() -> int:
    with config_lock:
        n = client_config["num_classes"]
    if n is None:
        raise HTTPException(
            status_code=503,
            detail="num_classes not configured; client must POST /config first",
        )
    return n


def find_pool(image_id: str) -> Optional[str]:
    return dm.find_pool(image_id)


def image_meta(image_id: str) -> dict:
    try:
        return dm.get_image_meta(image_id).to_dict()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")


# ---------------------------------------------------------------------------
# Training helpers
# ---------------------------------------------------------------------------
def update_training_status(patch: dict) -> None:
    with training_lock:
        training_status.update(patch)


def run_training_task(max_epochs: int, num_classes: int) -> None:
    pairs = dm.get_all_training_pairs()
    try:
        best_metric, version = train_model(
            training_pairs=pairs,
            model_save_path=config.BEST_MODEL_PATH,
            max_epochs=max_epochs,
            num_classes=num_classes,
            status_callback=update_training_status,
            cancel_event=training_cancel_event,
        )
        with training_lock:
            training_status["state"] = "completed"
            training_status["best_metric"] = best_metric
            training_status["version"] = version
            training_status["completed_at"] = datetime.now().isoformat(timespec="seconds")
            training_status["error"] = None
        logger.info("training completed: dice=%.4f, version=%s", best_metric, version)
    except TrainingCancelled:
        with training_lock:
            training_status["state"] = "cancelled"
            training_status["completed_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info("training cancelled")
    except Exception as e:  # noqa: BLE001
        logger.exception("training failed")
        with training_lock:
            training_status["state"] = "error"
            training_status["error"] = f"{type(e).__name__}: {e}"
            training_status["completed_at"] = datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
def load_persisted_config() -> None:
    cfg = load_client_config()
    if cfg is None:
        logger.info("no persisted client_config; awaiting POST /config")
        return
    with config_lock:
        client_config["palette"] = cfg["palette"]
        client_config["class_names"] = cfg["class_names"]
        client_config["num_classes"] = cfg["num_classes"]
    logger.info("loaded persisted config: num_classes=%d", cfg["num_classes"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/info")
def get_info() -> dict:
    stats = dm.get_stats()
    with config_lock:
        num_classes = client_config["num_classes"]
        class_names = client_config["class_names"]
        palette = client_config["palette"]
    total = stats["pending"] + stats["submitted"] + stats["fixed"]
    return {
        "name": SERVER_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "num_classes": num_classes if num_classes is not None else 0,
        "class_names": class_names if class_names is not None else [],
        "palette": palette if palette is not None else [],
        "input_size": config.IMAGE_SIZE,
        "counts": {
            "pending":   stats["pending"],
            "submitted": stats["submitted"],
            "fixed":     stats["fixed"],
            "total":     total,
        },
        "model": model_info(),
    }


@app.post("/config")
def post_config(cfg: ClientConfig) -> dict:
    if cfg.num_classes < 2:
        raise HTTPException(status_code=400, detail="num_classes must be >= 2")
    if len(cfg.palette) != cfg.num_classes:
        raise HTTPException(
            status_code=400, detail="palette length must equal num_classes"
        )
    if len(cfg.class_names) != cfg.num_classes:
        raise HTTPException(
            status_code=400, detail="class_names length must equal num_classes"
        )
    for rgb in cfg.palette:
        if len(rgb) != 3 or not all(isinstance(v, int) and 0 <= v <= 255 for v in rgb):
            raise HTTPException(
                status_code=400,
                detail="palette entries must be [R,G,B] with 0..255 integers",
            )

    stats = dm.get_stats()
    submitted_or_fixed = stats["submitted"] + stats["fixed"]
    with config_lock:
        prev_palette = client_config["palette"]
        if (
            prev_palette is not None
            and prev_palette != cfg.palette
            and submitted_or_fixed > 0
        ):
            raise HTTPException(
                status_code=409,
                detail="palette change forbidden while submitted/fixed pools are non-empty",
            )
        new_cfg = {
            "palette": [list(rgb) for rgb in cfg.palette],
            "class_names": list(cfg.class_names),
            "num_classes": int(cfg.num_classes),
        }
        client_config.update(new_cfg)
        save_client_config(new_cfg)

    logger.info("client config updated: num_classes=%d", cfg.num_classes)
    return {"status": "ok"}


@app.get("/images")
def list_images(pool: str = "pending") -> dict:
    if pool not in DataManager.POOLS:
        raise HTTPException(status_code=400, detail=f"unknown pool: {pool}")
    items = dm.list_pool_images(pool)
    return {"pool": pool, "count": len(items), "items": items}


@app.get("/images/{image_id}/meta")
def get_image_meta(image_id: str) -> dict:
    validate_image_id(image_id)
    return image_meta(image_id)


@app.get("/images/{image_id}/download")
def download_image(image_id: str):
    validate_image_id(image_id)
    try:
        path = dm.image_path(image_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    suffix = path.suffix.lstrip(".").lower()
    media_type = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/labels/{image_id}/download")
def download_label(image_id: str):
    validate_image_id(image_id)
    p = dm.get_label_path(image_id)
    if p is None:
        raise HTTPException(status_code=404, detail="label not found")
    # Protocol §5.1 mandates RGB PNG on the wire. Storage is implementation
    # detail: pending seeds (and fork-strict submitted masks) are stored as
    # single-channel class-id PNGs. Normalise to RGB via palette before
    # shipping. If the file is already RGB, FileResponse is sufficient.
    with Image.open(p) as im:
        if im.mode == "RGB":
            return FileResponse(p, media_type="image/png", filename=p.name)
    palette = require_palette()
    png_bytes = render_class_id_png_to_rgb(class_id_path=p, palette=palette)
    return Response(content=png_bytes, media_type="image/png")


@app.post("/infer/{image_id}")
def infer(image_id: str):
    validate_image_id(image_id)
    palette = require_palette()
    num_classes = require_num_classes()
    pool = find_pool(image_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="image not found")
    image_path = dm.image_path(image_id)

    # PRIMARY: trained model (5-fold ensemble or best.pt fallback)
    png = run_inference(
        image_path=image_path,
        model_path=config.BEST_MODEL_PATH,
        palette=palette,
        num_classes=num_classes,
    )
    if png is not None:
        return Response(
            content=png,
            media_type="image/png",
            headers={"X-Model-Source": "model"},
        )

    # FALLBACK: seed label (e.g. amodal-GT shipped with the dataset)
    seed = dm.seed_label_path(image_id)
    if seed is not None:
        try:
            with Image.open(image_path) as raw:
                target_size = raw.size  # (W, H)
            seed_png = render_class_id_png_to_rgb(
                class_id_path=seed,
                palette=palette,
                target_size=target_size,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("seed render failed for %s", image_id)
            raise HTTPException(status_code=500, detail=f"seed render failed: {e}")
        return Response(
            content=seed_png,
            media_type="image/png",
            headers={"X-Model-Source": "seed"},
        )

    raise HTTPException(
        status_code=503,
        detail="model not available and no seed label",
    )


@app.put("/submit/{image_id}")
async def submit_label(image_id: str, file: UploadFile = File(...)):
    validate_image_id(image_id)
    num_classes = require_num_classes()
    palette = require_palette()
    content = await file.read()
    try:
        status, pool = dm.submit(
            image_id, content, num_classes=num_classes, palette=palette
        )
    except ImageNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except PoolReadOnlyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": status, "image_id": image_id, "pool": pool}


@app.get("/next")
def get_next(strategy: str = "random") -> dict:
    try:
        image_id = dm.get_next_pending(strategy=strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if image_id is None:
        return {"image_id": None}
    return image_meta(image_id)


@app.post("/train")
def start_training(
    background_tasks: BackgroundTasks,
    max_epochs: int = config.DEFAULT_MAX_EPOCHS,
) -> dict:
    num_classes = require_num_classes()
    pairs = dm.get_all_training_pairs()
    if len(pairs) < config.MIN_IMAGES_FOR_TRAINING:
        raise HTTPException(
            status_code=400,
            detail=(
                f"insufficient training pairs: have {len(pairs)}, "
                f"need >= {config.MIN_IMAGES_FOR_TRAINING}"
            ),
        )

    n_folds = min(config.N_FOLDS, max(2, len(pairs)))

    with training_lock:
        if training_status["state"] == "running":
            raise HTTPException(status_code=409, detail="training already running")
        training_status.update({
            "state": "running",
            "epoch": 0,
            "max_epochs": n_folds * max_epochs,
            "best_metric": 0.0,
            "metric_name": DEFAULT_METRIC_NAME,
            "current_fold": 0,
            "n_folds": n_folds,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "version": None,
            "error": None,
        })

    training_cancel_event.clear()
    background_tasks.add_task(run_training_task, max_epochs, num_classes)
    logger.info(
        "training started: max_epochs=%d, pairs=%d, num_classes=%d",
        max_epochs, len(pairs), num_classes,
    )
    return {
        "status": "started",
        "max_epochs": max_epochs,
        "training_pairs": len(pairs),
    }


@app.post("/train/cancel")
def cancel_training() -> dict:
    with training_lock:
        if training_status["state"] != "running":
            raise HTTPException(status_code=409, detail="training is not running")
    training_cancel_event.set()
    logger.info("training cancel requested")
    return {"status": "cancelling"}


@app.get("/status")
def get_training_status() -> dict:
    with training_lock:
        snapshot = dict(training_status)
    if snapshot["state"] == "idle":
        return {"state": "idle"}
    return {k: v for k, v in snapshot.items() if v is not None}


@app.get("/models/latest")
def download_latest_model():
    if not config.COREML_ZIP_PATH.exists():
        raise HTTPException(status_code=404, detail="no CoreML model available")
    return FileResponse(
        config.COREML_ZIP_PATH,
        media_type="application/zip",
        filename=config.COREML_ZIP_PATH.name,
        headers=latest_headers(),
    )


def _convert_coreml_task() -> None:
    py = os.environ.get("PYTHON", "python")
    cmd = [py, "convert_coreml.py"]
    logger.info("invoking %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("CoreML conversion failed to launch: %s", e)
        return
    ok = result.returncode == 0 and config.COREML_ZIP_PATH.exists()
    if ok:
        info = bump_version()
        logger.info("CoreML conversion ok: version=%s", info.get("version"))
    else:
        logger.error("CoreML conversion exited rc=%d", result.returncode)
        logger.error("stderr: %s", result.stderr[-1000:])


@app.post("/models/convert")
def start_conversion(background_tasks: BackgroundTasks) -> dict:
    if not config.BEST_MODEL_PATH.exists():
        raise HTTPException(
            status_code=400,
            detail="no trained PyTorch checkpoint to convert",
        )
    background_tasks.add_task(_convert_coreml_task)
    return {"status": "started"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload=False,
    )
