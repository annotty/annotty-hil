# Annotty HIL Server (PeriorbitAI)

Annotty-HIL **protocol v1.0** reference implementation, configured for the
periocular 7-class segmentation workflow.

The server itself is **class-agnostic**: the palette, class names, and
class count are supplied at runtime via `POST /config` (or persisted on
disk in `data/client_config.json`). The same binary can drive any
multi-class segmentation task — periocular today, fundus vessels
tomorrow.

## Periocular default classes

| ID | Class           | Color   |
|----|-----------------|---------|
| 0  | background      | black   |
| 1  | brow            | green   |
| 2  | sclera          | violet  |
| 3  | exposed_iris    | yellow  |
| 4  | caruncle        | magenta |
| 5  | lid             | cyan    |
| 6  | occluded_iris   | orange  |

Run `scripts/init_periocular_config.py` to seed `data/client_config.json`
with this layout.

## Layout

```
server/
├── main.py              FastAPI v1.0 entry point
├── config.py            paths, hyperparameters (no class constants)
├── model.py             smp.Unet factory (num_classes parameter)
├── data_manager.py      filesystem CRUD for unannotated/completed pools
├── dataset.py           PyTorch Dataset + albumentations augmentations
├── trainer.py           train_model() — 5-fold CV, CE + multiclass Dice
├── inference.py         5-fold ensemble argmax → palette LUT → RGB PNG
├── convert_coreml.py    PyTorch -> CoreML export (run inside WSL2)
├── version_manager.py   model version + MD5 + X-Model-* headers
├── requirements.txt
├── scripts/
│   ├── migrate_legacy_dirs.py    one-shot: legacy fork dirs → v1.0
│   ├── init_periocular_config.py one-shot: write 7-class client_config.json
│   ├── derive_palette_from_labels.py  scan labels → propose config
│   ├── import_images.py          bulk import periocular_dataset
│   └── test_api.py               E2E HTTP smoke test
└── data/                  (gitignored) created on first run
    ├── client_config.json    persisted POST /config
    ├── unannotated/
    │   ├── images/
    │   └── annotations/      seed labels (e.g. amodal-GT)
    ├── completed/
    │   ├── images/
    │   └── annotations/      finalised masks (training corpus)
    ├── models/
    │   ├── pytorch/          best.pt, fold{1..5}.pt
    │   └── coreml/           model.mlpackage, model.mlpackage.zip
    ├── static/
    └── logs/                 server.log, train_status.json
```

## Quick start (Windows)

```bash
cd E:\PeriorbitAI\server

# 1. one-time setup
uv venv .venv --python 3.10
uv pip install -r requirements.txt

# 2. (only if you have legacy fork dirs) migrate to v1.0 layout
.venv\Scripts\python.exe scripts\migrate_legacy_dirs.py

# 3. seed the periocular 7-class config
.venv\Scripts\python.exe scripts\init_periocular_config.py

# 4. import the dataset as the seed pool
.venv\Scripts\python.exe scripts\import_images.py

# 5. start the server
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000

# 6. (in another terminal) sanity-check it
.venv\Scripts\python.exe scripts\test_api.py --base http://127.0.0.1:8000
```

For a different segmentation task swap step 3 with your own config (or
use `scripts/derive_palette_from_labels.py path/to/labels/` to auto-propose
one).

## HTTP API (protocol v1.0)

| Method | Path                          | Purpose                                              |
|--------|-------------------------------|------------------------------------------------------|
| GET    | `/info`                       | name, protocol_version, classes, palette, counts, model info |
| POST   | `/config`                     | configure palette / class_names / num_classes (persisted) |
| GET    | `/images?pool={unannotated,completed}` | list images in a pool                          |
| GET    | `/images/{id}/meta`           | pool / has_seed / has_annotation / dimensions / size |
| GET    | `/images/{id}/download`       | binary image                                         |
| GET    | `/labels/{id}/download`       | finalised mask PNG                                   |
| POST   | `/infer/{id}`                 | RGB PNG (palette applied), `X-Model-Source: model\|seed` |
| PUT    | `/submit/{id}`                | upload finalised mask (multipart `file`)             |
| GET    | `/next?strategy={random,sequential}` | next unlabeled image (image_meta shape)       |
| POST   | `/train`                      | start 5-fold CV training (background)                |
| POST   | `/train/cancel`               | cancel a running training run                        |
| GET    | `/status`                     | training progress (state-keyed dict)                 |
| POST   | `/models/convert`             | invoke `python convert_coreml.py` in the background  |
| GET    | `/models/latest`              | download the CoreML zip + `X-Model-*` headers        |

### POST /config body

```json
{
  "num_classes": 7,
  "class_names": ["background", "brow", "sclera", "exposed_iris",
                  "caruncle", "lid", "occluded_iris"],
  "palette": [[0,0,0],[0,230,0],[130,0,235],[255,230,0],
              [255,0,230],[0,230,230],[255,130,0]]
}
```

Constraints: `num_classes >= 2`; `len(palette) == len(class_names) ==
num_classes`; each palette entry is `[R,G,B]` with integers `0..255`.
Returns `409 Conflict` if you try to change the palette while
`completed/` is non-empty (would corrupt class IDs in stored masks).

### POST /infer behaviour

Tries the trained model first (5-fold ensemble averaged softmax → argmax,
falls back to `best.pt` if no fold checkpoints). When no model is
available the server falls back to the seed mask under
`unannotated/annotations/{id}` (e.g. the amodal-GT predictions imported
with the dataset). If both are missing the server returns `503`. The
response header `X-Model-Source` is `"model"` or `"seed"` so the client
can distinguish.

### Mask payload (PUT /submit)

Single-channel PNG, uint8, values `0..num_classes-1`. Three-channel PNGs
are accepted only if all channels are equal.

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

The server is **unauthenticated** — protect the hostname with Cloudflare
Access (Zero Trust) policy or a Cloudflare Worker before going public.

## Verification checklist

After `scripts/import_images.py` and `scripts/init_periocular_config.py`:

- `GET /info` reports `protocol_version="1.0"`, `num_classes=7`,
  `counts.unannotated > 0`
- `POST /infer/{id}` returns an RGB PNG; header `X-Model-Source: seed`
  (no model trained yet)

After training:

- `GET /status` shows `state="completed"`, `best_metric > 0`,
  `version != null`
- `POST /infer/{id}` now returns header `X-Model-Source: model`
- After `POST /models/convert` the CoreML zip is fetchable at
  `GET /models/latest` with `X-Model-Version`, `X-Model-Md5`, and
  `X-Model-Updated-At` headers
