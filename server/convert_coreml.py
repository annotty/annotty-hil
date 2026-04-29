"""PyTorch -> CoreML conversion for the Annotty HIL v1.0 U-Net.

Run inside **WSL2 (Ubuntu)** because ``coremltools`` is most reliable on
real Linux/macOS::

    cd /mnt/e/PeriorbitAI/server
    python convert_coreml.py [--input data/models/pytorch/best.pt]
                             [--output data/models/coreml/model.mlpackage]
                             [--zip-output data/models/coreml/model.mlpackage.zip]
                             [--num-classes 7]

The number of classes is read from ``data/client_config.json`` if not
supplied via ``--num-classes``. The wrapped model emits an argmax class-id
image; the iPad client can apply its palette client-side.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

import torch

import config
from model import create_model


class ArgmaxWrapper(torch.nn.Module):
    """Wrap a logits-emitting model to emit a class-id image."""

    def __init__(self, base: torch.nn.Module):
        super().__init__()
        self.base = base

    def forward(self, x):
        logits = self.base(x)
        return torch.argmax(logits, dim=1, keepdim=False).to(torch.uint8)


def _num_classes_from_config_json() -> int | None:
    if not config.CLIENT_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(config.CLIENT_CONFIG_PATH.read_text(encoding="utf-8"))
        n = int(data.get("num_classes"))
        return n if n >= 2 else None
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=config.BEST_MODEL_PATH)
    p.add_argument("--output", type=Path, default=config.COREML_PATH)
    p.add_argument("--zip-output", type=Path, default=config.COREML_ZIP_PATH)
    p.add_argument("--input-size", type=int, default=config.IMAGE_SIZE)
    p.add_argument("--num-classes", type=int, default=None,
                   help="Override num_classes (otherwise read from client_config.json)")
    return p.parse_args()


def main() -> int:
    try:
        import coremltools as ct  # type: ignore
    except ImportError:
        print(
            "coremltools not installed. In WSL2 run: pip install 'coremltools>=8.0'",
            file=sys.stderr,
        )
        return 1

    args = parse_args()
    if not args.input.exists():
        print(f"weights not found at {args.input}", file=sys.stderr)
        return 1

    num_classes = args.num_classes or _num_classes_from_config_json()
    if num_classes is None:
        print(
            "num_classes not provided and data/client_config.json missing/invalid. "
            "Pass --num-classes N or run scripts/init_periocular_config.py.",
            file=sys.stderr,
        )
        return 2

    base = create_model(num_classes=num_classes)
    state = torch.load(args.input, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    base.load_state_dict(state)
    base.eval()
    model = ArgmaxWrapper(base).eval()

    dummy = torch.zeros(1, config.IN_CHANNELS, args.input_size, args.input_size)
    print(f"tracing model on input {tuple(dummy.shape)} (num_classes={num_classes}) ...")
    traced = torch.jit.trace(model, dummy)

    print("converting to CoreML ...")
    mlmodel = ct.convert(
        traced,
        inputs=[ct.ImageType(
            name="image",
            shape=dummy.shape,
            scale=1.0 / 255.0,
            bias=[-m / s for m, s in zip(config.IMAGENET_MEAN, config.IMAGENET_STD)],
        )],
        outputs=[ct.TensorType(name="label")],
        compute_precision=ct.precision.FLOAT16,
        convert_to="mlprogram",
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.is_dir():
            shutil.rmtree(args.output)
        else:
            args.output.unlink()
    mlmodel.save(str(args.output))
    print(f"wrote {args.output}")

    print(f"zipping to {args.zip_output} ...")
    args.zip_output.parent.mkdir(parents=True, exist_ok=True)
    if args.zip_output.exists():
        args.zip_output.unlink()
    with zipfile.ZipFile(args.zip_output, "w", zipfile.ZIP_DEFLATED) as z:
        for p in args.output.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(args.output.parent))
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
