# Annotty HIL Server (reference implementation)

Reference implementation of the Annotty HIL protocol
([`protocol/protocol.md`](../protocol/protocol.md), v1.1), used for the
periocular multi-class segmentation workflow. It passes
[`protocol/conformance_test.py`](../protocol/conformance_test.py).

## Class definition

The server owns the class definition (protocol §5.2). It is read from
`data/client_config.json` at startup; the server refuses to start without it.

```json
{
  "num_classes": 3,
  "class_names": ["background", "brow", "lid"],
  "palette": [[255,255,255],[255,0,0],[255,128,0]]
}
```

The `palette` here is only the default. The iPad client replaces it via
`POST /config` (class names and count must match, otherwise 409). Labels are
stored as class-id PNGs, so the palette can change at any time.
`scripts/derive_palette_from_labels.py path/to/labels/` proposes this file
from existing class-id masks.

## Layout

```
server/
├── main.py              FastAPI entry point (protocol endpoints)
├── config.py            paths, hyperparameters (no class constants)
├── model.py             smp.Unet factory (num_classes parameter)
├── data_manager.py      3-pool filesystem layout (pending / submitted / fixed)
├── dataset.py           PyTorch Dataset + albumentations augmentations
├── trainer.py           train_model() — 5-fold CV, CE + multiclass Dice
├── inference.py         5-fold ensemble argmax → palette LUT → RGB PNG
├── convert_coreml.py    PyTorch -> CoreML export (run inside WSL2)
├── version_manager.py   model version + MD5 + X-Model-* headers
├── requirements.txt
├── scripts/
│   ├── derive_palette_from_labels.py  scan class-id masks → propose client_config.json
│   ├── import_images.py               bulk import a dataset into the pending pool
│   └── smooth_labels.py               pre-render labels at display resolution (protocol §9)
└── data/                (gitignored) created on first run
    ├── client_config.json    class definition + current palette
    ├── pending/{images,labels}/     HITL queue (labels = optional seeds)
    ├── submitted/{images,labels}/   submitted by the iPad (training data)
    ├── fixed/{images,labels}/       read-only finished data (training data)
    ├── models/{pytorch,coreml}/
    └── logs/                 server.log, train_status.json
```

## Quick start

```bash
cd server
pip install -r requirements.txt

# 1. write data/client_config.json (see "Class definition")
# 2. import images into the pending pool
python scripts/import_images.py
# 3. start the server
python -m uvicorn main:app --host 0.0.0.0 --port 8000
# 4. (another terminal) check protocol conformance
python ../protocol/conformance_test.py http://127.0.0.1:8000
```

## Implementation notes

- `POST /infer/{id}` uses the trained model (5-fold ensemble, falling back to
  `best.pt`). With no model it returns the seed label if one exists, else 503.
  The `X-Model-Source` header (`model` / `seed`) tells which.
- `PUT /submit/{id}` rejects masks containing colours outside the current
  palette (400), as required by protocol §5.1.

## Training notes

- `CrossEntropyLoss + DiceLoss(mode="multiclass")` per batch.
- Dice metric reported in `/status.best_metric` is the mean over
  **foreground** classes (background excluded), so the value is
  comparable across runs.
- 5-fold CV uses `sklearn.model_selection.KFold(shuffle=True,
  random_state=42)`. The fold with the highest validation Dice is copied
  to `best.pt`.
- The runner is a FastAPI `BackgroundTasks` with a `threading.Event` for
  cancellation.

## CoreML export — runs inside **WSL2 (Ubuntu)**

`coremltools >= 8.0` is the most reliable on Linux/macOS. The host
Windows venv does *not* install it; the conversion runs inside WSL2.

```bash
cd /mnt/e/PeriorbitAI/server
source .venv-wsl/bin/activate
python convert_coreml.py
# -> data/models/coreml/model.mlpackage
# -> data/models/coreml/model.mlpackage.zip   (served by /models/latest)
```

`POST /models/convert` shells out to whatever Python is on `PATH`; if you
want it to use the WSL venv set `PYTHON=...` in the environment that
launches the server, or trigger the conversion manually in WSL.

## Cloudflare Tunnel (public exposure)

Use a Quick Tunnel for testing, a named tunnel for permanent deployment.

```bash
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
# -> https://<random>.trycloudflare.com
```

This server does not implement `X-API-Key` (optional in protocol §3) — protect
the hostname with Cloudflare Access (Zero Trust) or a Cloudflare Worker before
going public.
