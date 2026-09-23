# Annotty HIL (Human-in-the-Loop)

**iPad annotation app with Human-in-the-Loop active learning for medical image segmentation.**

> **Building a server for this app?** Read [`protocol/`](protocol/) first — it contains the
> spec and a conformance test. [`server/`](server/) is the reference implementation.

Annotty HIL combines on-device AI inference (CoreML) with server-side training to create a fast, iterative annotation workflow. Designed for retinal fundus vessel segmentation, but adaptable to any binary/multi-class segmentation task.

## The Problem

Manual pixel-level annotation of medical images is extremely time-consuming. A single retinal image can take 30+ minutes to annotate by hand. Traditional workflows require annotating hundreds of images before training a model.

## Before → After

```
BEFORE: Traditional Annotation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Annotate 100 images (50+ hours)
           ↓
  Train model on server
           ↓
  Evaluate results
           ↓
  Annotate 100 more... 😩

AFTER: Annotty HIL
━━━━━━━━━━━━━━━━━━
  ┌─────────────────────────────────────┐
  │  1. AI Predict (on-device, <1 sec)  │
  │  2. Fix mistakes (2-5 min)          │
  │  3. Submit to server                │
  │  4. Train (server-side)             │
  │  5. Repeat with better predictions  │
  └─────────────────────────────────────┘
  Each cycle: model gets smarter → less manual work 🚀
```

| | Traditional | Annotty HIL |
|---|---|---|
| Time per image | 30-60 min | 2-5 min |
| Feedback loop | Days/weeks | Minutes |
| Inference hardware | Desktop GPU | iPad (on-device CoreML) |
| Training hardware | Desktop GPU | PC with GPU or cloud |
| Works offline | No | AI prediction works offline |

## Architecture

```
┌─────────────────┐          ┌──────────────────┐
│   iPad App      │  HTTPS   │  Server (PC)     │
│                 │◄────────►│                   │
│  SwiftUI + Metal│ Cloudflare│  FastAPI          │
│  CoreML (U-Net) │  Tunnel  │  PyTorch training │
│                 │          │  coremltools      │
└─────────────────┘          └──────────────────┘

iPad:   Load image → AI Predict → Annotate → Submit
Server: Collect labels → Train → Convert to CoreML → Deliver
```

## Features

### iPad App
- **Metal rendering** — 60fps pan/zoom/rotate on large images
- **On-device CoreML inference** — U-Net segmentation in <1 second
- **Multi-class annotation** — 8 color classes with fill, brush, and eraser tools
- **Smart edge tracing** — Smooth tool for precise boundary annotation
- **HIL integration** — Download images, submit labels, trigger training
- **Export** — PNG masks, COCO JSON, YOLO format

### Server
- **FastAPI** — RESTful API for image management and training
- **Active learning** — Recommends the most informative images to annotate next
- **Cloudflare Tunnel** — Secure HTTPS connection without port forwarding
- **CoreML conversion** — Automatically converts trained PyTorch models for iPad

## Quick Setup

### Requirements

| Component | Requirement |
|-----------|-------------|
| iPad App | iPad with A12+ chip, iOS 17+ |
| Server | Python 3.10+, PyTorch, 8GB+ RAM |
| Connection | Both devices on internet (Cloudflare Tunnel) |

### 1. iPad App

```bash
git clone https://github.com/annotty/annotty-hil.git
cd annotty-hil
```

Open `AnnottyHIL.xcodeproj` in Xcode, select your iPad, and run.

> **Using xcodegen (optional):** If you modify `project.yml`, regenerate with `xcodegen generate`.

### 2. Server (Windows/Mac/Linux)

The iPad app talks to any server that implements [`protocol/`](protocol/).
The bundled reference server is in [`server/`](server/) — see its README for setup.

```bash
cd server
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
# expose it over HTTPS (copy the printed https://xxxx.trycloudflare.com URL)
cloudflared tunnel --url http://localhost:8000
```

### 3. Connect iPad to Server

1. Open Annotty HIL on iPad
2. Tap **Load** → **Cloudflare Settings**
3. Enter the server URL and toggle **Enable HIL**
4. Tap **Test Connection** — should show image count

### 4. Annotation Workflow

```
Load image from server → AI Predict → Fix with brush → Submit → Next image
                                                         ↓
                                            When ready → Train
```

## API

See [`protocol/protocol.md`](protocol/protocol.md).

## Project Structure

```
annotty-hil/
├── AnnottyHIL/                 # iOS app source
│   ├── Metal/                  # Metal shaders & renderer
│   ├── Services/
│   │   ├── HIL/                # Server client, settings, cache
│   │   └── UNet/Models/        # CoreML models (Git LFS)
│   ├── ViewModels/             # App state & logic
│   └── Views/                  # SwiftUI views
├── protocol/                   # Client ⇄ server spec + conformance test (start here for servers)
├── server/                     # Reference server (Python / FastAPI)
├── project.yml                 # xcodegen spec
└── AnnottyHIL.xcodeproj        # Xcode project
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| UI | SwiftUI |
| Rendering | Metal + custom shaders |
| AI Inference | CoreML (on-device) |
| Training | PyTorch (server-side) |
| Model Conversion | coremltools |
| Networking | Cloudflare Tunnel (HTTPS) |
| API | FastAPI |

## License

MIT License. See [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.
