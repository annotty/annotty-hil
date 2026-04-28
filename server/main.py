"""
Annotty HIL Server — protocol v1.0 reference implementation (3-pool revision)
docs/protocol.md を本実装の単一真実源とする。
"""
import os
import re
import time
import hashlib
import logging
import threading
import shutil
import tempfile
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import uvicorn

from config import (
    BEST_MODEL_PATH, COREML_PATH, LOG_DIR,
    STATIC_DIR, SERVER_HOST, SERVER_PORT, MIN_IMAGES_FOR_TRAINING,
    DEFAULT_MAX_EPOCHS, N_FOLDS, IMAGE_SIZE,
    PENDING_IMAGES_DIR, PENDING_LABELS_DIR,
    SUBMITTED_IMAGES_DIR, SUBMITTED_LABELS_DIR,
    FIXED_IMAGES_DIR, FIXED_LABELS_DIR,
)
from data_manager import (
    DataManager, ImageNotFoundError, PoolReadOnlyError,
)

PROTOCOL_VERSION = "1.0"
SERVER_NAME = "Annotty HIL Server"
DEFAULT_METRIC_NAME = "dice"

# === ログ設定 ===
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "server.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app = FastAPI(title=SERVER_NAME, version=PROTOCOL_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Model-Version", "X-Model-Md5", "X-Model-Updated-At"],
)

dm = DataManager()

# === クライアント由来の動的設定（POST /config で更新） ===
client_config: dict = {
    "palette": None,        # list[list[int]] | None
    "class_names": None,    # list[str] | None
    "num_classes": None,    # int | None
}
config_lock = threading.Lock()

# === 訓練ステータス（protocol v1 §7.12 準拠） ===
training_status: dict = {
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
training_lock = threading.Lock()
training_cancel_event = threading.Event()


# =====================================================
# 共通ヘルパー
# =====================================================
_IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]+\.(png|jpg|jpeg)$", re.IGNORECASE)


def validate_image_id(image_id: str) -> None:
    if ".." in image_id or "/" in image_id or "\\" in image_id:
        raise HTTPException(status_code=400, detail="invalid image_id")
    if not _IMAGE_ID_RE.match(image_id):
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


def file_md5(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_info() -> dict:
    coreml_exists = os.path.exists(COREML_PATH)
    if coreml_exists:
        updated_at = os.path.getmtime(COREML_PATH)
        version = datetime.fromtimestamp(updated_at).strftime("%Y%m%d-%H%M%S")
        manifest = os.path.join(COREML_PATH, "Manifest.json")
        md5 = file_md5(manifest) if os.path.exists(manifest) else None
    else:
        updated_at = 0.0
        version = "0"
        md5 = None
    return {
        "best_exists": os.path.exists(BEST_MODEL_PATH),
        "coreml_exists": coreml_exists,
        "version": version,
        "updated_at": updated_at,
        "md5": md5,
    }


def image_meta(image_id: str) -> dict:
    pool = dm.find_pool(image_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="image not found")

    img_path = dm.get_image_path(image_id)
    width, height = 0, 0
    try:
        from PIL import Image
        with Image.open(img_path) as im:
            width, height = im.size
    except Exception:
        pass

    return {
        "image_id": image_id,
        "pool": pool,
        "has_seed": dm.has_seed(image_id) if pool == "pending" else False,
        "has_annotation": dm.has_annotation(image_id),
        "bytes": os.path.getsize(img_path),
        "width": width,
        "height": height,
    }


# =====================================================
# §7.1 GET /info
# =====================================================
@app.get("/info")
def get_info():
    stats = dm.get_stats()
    with config_lock:
        num_classes = client_config["num_classes"]
        class_names = client_config["class_names"]
    return {
        "name": SERVER_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "num_classes": num_classes if num_classes is not None else 0,
        "class_names": class_names if class_names is not None else [],
        "input_size": IMAGE_SIZE,
        "counts": {
            "pending": stats["pending"],
            "submitted": stats["submitted"],
            "fixed": stats["fixed"],
            "total": stats["pending"] + stats["submitted"] + stats["fixed"],
        },
        "model": model_info(),
    }


# =====================================================
# §7.2 POST /config
# =====================================================
class ClientConfig(BaseModel):
    palette: list[list[int]]
    class_names: list[str]
    num_classes: int


@app.post("/config")
def post_config(cfg: ClientConfig):
    if cfg.num_classes < 2:
        raise HTTPException(status_code=400, detail="num_classes must be >= 2")
    if len(cfg.palette) != cfg.num_classes:
        raise HTTPException(status_code=400, detail="palette length must equal num_classes")
    if len(cfg.class_names) != cfg.num_classes:
        raise HTTPException(status_code=400, detail="class_names length must equal num_classes")
    for rgb in cfg.palette:
        if len(rgb) != 3 or not all(0 <= v <= 255 for v in rgb):
            raise HTTPException(status_code=400, detail="palette entries must be [R,G,B] in 0..255")

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
        client_config["palette"] = cfg.palette
        client_config["class_names"] = cfg.class_names
        client_config["num_classes"] = cfg.num_classes

    logger.info(f"client config updated: num_classes={cfg.num_classes}")
    return {"status": "ok"}


# =====================================================
# §7.3 GET /images
# =====================================================
@app.get("/images")
def list_images(pool: str = "pending"):
    if pool not in DataManager.POOLS:
        raise HTTPException(status_code=400, detail=f"unknown pool: {pool}")
    items = dm.list_pool_images(pool)
    return {"pool": pool, "count": len(items), "items": items}


# =====================================================
# §7.4 GET /images/{id}/meta
# =====================================================
@app.get("/images/{image_id}/meta")
def get_image_meta(image_id: str):
    validate_image_id(image_id)
    return image_meta(image_id)


# =====================================================
# §7.5 GET /images/{id}/download
# =====================================================
@app.get("/images/{image_id}/download")
def download_image(image_id: str):
    validate_image_id(image_id)
    path = dm.get_image_path(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    media_type = "image/jpeg" if image_id.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return FileResponse(path, media_type=media_type)


# =====================================================
# §7.6 GET /labels/{id}/download (検索順: submitted → fixed → pending)
# =====================================================
@app.get("/labels/{image_id}/download")
def download_label(image_id: str):
    validate_image_id(image_id)
    path = dm.get_label_path(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="label not found")
    return FileResponse(path, media_type="image/png")


# =====================================================
# §7.7 POST /infer/{id}
# =====================================================
@app.post("/infer/{image_id}")
def infer(image_id: str):
    validate_image_id(image_id)
    palette = require_palette()
    image_path = dm.get_image_path(image_id)
    if image_path is None:
        raise HTTPException(status_code=404, detail="image not found")
    if not os.path.exists(BEST_MODEL_PATH):
        raise HTTPException(status_code=503, detail="model not available, train first")

    try:
        from inference import run_inference
        png_bytes = run_inference(image_path, BEST_MODEL_PATH, palette=palette)
        if png_bytes is None:
            raise HTTPException(status_code=503, detail="model not available, train first")
        return Response(content=png_bytes, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"infer error: {image_id} - {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# §7.8 PUT /submit/{id}
# =====================================================
@app.put("/submit/{image_id}")
async def submit_label(image_id: str, file: UploadFile = File(...)):
    validate_image_id(image_id)
    require_palette()
    content = await file.read()
    try:
        status, pool = dm.submit(image_id, content)
    except ImageNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    except PoolReadOnlyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": status, "image_id": image_id, "pool": pool}


# =====================================================
# §7.9 GET /next (pending only)
# =====================================================
@app.get("/next")
def get_next(strategy: str = "random"):
    image_id = dm.get_next_pending(strategy=strategy)
    if image_id is None:
        return {"image_id": None}
    return image_meta(image_id)


# =====================================================
# §7.10 POST /train
# =====================================================
@app.post("/train")
def start_training(background_tasks: BackgroundTasks, max_epochs: int = DEFAULT_MAX_EPOCHS):
    training_pairs = dm.get_all_training_pairs()
    if len(training_pairs) < MIN_IMAGES_FOR_TRAINING:
        raise HTTPException(
            status_code=400,
            detail=(
                f"insufficient training pairs: have {len(training_pairs)}, "
                f"need >= {MIN_IMAGES_FOR_TRAINING}"
            ),
        )

    with training_lock:
        if training_status["state"] == "running":
            raise HTTPException(status_code=409, detail="training already running")
        training_status.update({
            "state": "running",
            "epoch": 0,
            "max_epochs": N_FOLDS * max_epochs,
            "best_metric": 0.0,
            "metric_name": DEFAULT_METRIC_NAME,
            "current_fold": 0,
            "n_folds": N_FOLDS,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "completed_at": None,
            "version": None,
            "error": None,
        })

    training_cancel_event.clear()
    logger.info(f"training started: max_epochs={max_epochs}, pairs={len(training_pairs)}")
    background_tasks.add_task(run_training_task, training_pairs, max_epochs)
    return {
        "status": "started",
        "max_epochs": max_epochs,
        "training_pairs": len(training_pairs),
    }


def run_training_task(training_pairs: list[tuple[str, str]], max_epochs: int):
    from trainer import train_model, TrainingCancelled
    try:
        best_metric, version = train_model(
            training_pairs=training_pairs,
            model_save_path=BEST_MODEL_PATH,
            max_epochs=max_epochs,
            status_callback=update_training_status,
            cancel_event=training_cancel_event,
        )
        with training_lock:
            training_status["state"] = "completed"
            training_status["best_metric"] = best_metric
            training_status["version"] = version
            training_status["completed_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info(f"training completed: best_metric={best_metric:.4f}, version={version}")
    except TrainingCancelled:
        with training_lock:
            training_status["state"] = "cancelled"
            training_status["completed_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info("training cancelled")
    except Exception as e:
        with training_lock:
            training_status["state"] = "error"
            training_status["error"] = str(e)
            training_status["completed_at"] = datetime.now().isoformat(timespec="seconds")
        logger.error(f"training error: {e}")


def update_training_status(epoch: int, metric: float, fold_idx: int = 0):
    with training_lock:
        training_status["epoch"] = epoch
        if training_status["best_metric"] is None:
            training_status["best_metric"] = metric
        else:
            training_status["best_metric"] = max(training_status["best_metric"], metric)
        training_status["current_fold"] = fold_idx


# =====================================================
# §7.11 POST /train/cancel
# =====================================================
@app.post("/train/cancel")
def cancel_training():
    with training_lock:
        if training_status["state"] != "running":
            raise HTTPException(status_code=409, detail="training is not running")
    training_cancel_event.set()
    logger.info("training cancel requested")
    return {"status": "cancelling"}


# =====================================================
# §7.12 GET /status
# =====================================================
@app.get("/status")
def get_training_status():
    with training_lock:
        snapshot = dict(training_status)
    if snapshot["state"] == "idle":
        return {"state": "idle"}
    return {k: v for k, v in snapshot.items() if v is not None}


# =====================================================
# §7.13 GET /models/latest
# =====================================================
@app.get("/models/latest")
def download_latest_model():
    if not os.path.exists(COREML_PATH):
        raise HTTPException(status_code=404, detail="CoreML model not available, convert first")

    info = model_info()
    try:
        tmp_dir = tempfile.mkdtemp()
        zip_base = os.path.join(tmp_dir, "SegmentationModel.mlpackage")
        zip_path = shutil.make_archive(zip_base, "zip", COREML_PATH)
        headers = {
            "X-Model-Version": str(info["version"]),
            "X-Model-Md5": str(info["md5"] or ""),
            "X-Model-Updated-At": str(info["updated_at"]),
        }
        logger.info(f"serving CoreML model: {zip_path}")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename="SegmentationModel.mlpackage.zip",
            headers=headers,
        )
    except Exception as e:
        logger.error(f"CoreML serve error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# §7.14 POST /models/convert
# =====================================================
@app.post("/models/convert")
def start_conversion(background_tasks: BackgroundTasks):
    if not os.path.exists(BEST_MODEL_PATH):
        raise HTTPException(status_code=404, detail="PyTorch model not found, train first")
    with training_lock:
        if training_status["state"] == "running":
            raise HTTPException(status_code=409, detail="training in progress, wait for completion")

    background_tasks.add_task(run_conversion_task)
    logger.info("CoreML conversion requested")
    return {"status": "conversion_started"}


def run_conversion_task():
    try:
        from convert_coreml import convert_to_coreml
        convert_to_coreml()
        logger.info("CoreML conversion completed")
    except Exception as e:
        logger.error(f"CoreML conversion error: {e}")


# =====================================================
# 静的ファイル配信（任意）
# =====================================================
if os.path.isdir(STATIC_DIR) and os.listdir(STATIC_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/web", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    logger.info(f"static files: /web → {STATIC_DIR}")


# =====================================================
# エントリーポイント
# =====================================================
if __name__ == "__main__":
    logger.info(f"starting {SERVER_NAME} (protocol v{PROTOCOL_VERSION})")
    logger.info(f"listening on http://{SERVER_HOST}:{SERVER_PORT}")
    stats = dm.get_stats()
    logger.info(
        f"data: pending={stats['pending']}, "
        f"submitted={stats['submitted']}, "
        f"fixed={stats['fixed']}"
    )
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
