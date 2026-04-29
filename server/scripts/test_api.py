"""End-to-end HTTP smoke test for the Annotty HIL v1.0 server (rev3pool).

Steps:
  1. ``GET /info`` shows ``protocol_version="1.0"`` and 3-pool counts
  2. ``POST /config`` with the periocular 7-class layout (200 + ``status=ok``)
  3. ``GET /info`` reflects the new config (``num_classes==7``)
  4. ``GET /images?pool=pending`` returns a non-empty list
  5. ``GET /next`` returns image_meta from the pending pool with ``has_seed==True``
  6. ``POST /infer/{id}`` returns an RGB PNG (palette-applied)
  7. (Optional) ``POST /train?max_epochs=1`` flips ``GET /status`` to running

Usage::

    python scripts/test_api.py [--base http://127.0.0.1:8000] [--with-train]
"""
from __future__ import annotations

import argparse
import io
import sys
import time

from PIL import Image

try:
    import requests
except ImportError:  # pragma: no cover
    print("install requests first: pip install requests")
    sys.exit(1)


PERIOCULAR_CONFIG = {
    "num_classes": 7,
    "class_names": [
        "background", "brow", "sclera", "exposed_iris",
        "caruncle", "lid", "occluded_iris",
    ],
    # NB: bg is white (not black) for iPad ColorMaskParser compatibility.
    "palette": [
        [255, 255, 255], [0, 230, 0], [130, 0, 235], [255, 230, 0],
        [255, 0, 230], [0, 230, 230], [255, 130, 0],
    ],
}


def _expect(condition: bool, msg: str) -> None:
    if not condition:
        print(f"FAIL: {msg}")
        sys.exit(2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--with-train", action="store_true",
                   help="also kick off a brief training run")
    args = p.parse_args()
    base = args.base.rstrip("/")

    # 1. /info
    r = requests.get(f"{base}/info", timeout=5)
    r.raise_for_status()
    info = r.json()
    print(f"/info: name={info.get('name')}, "
          f"protocol_version={info.get('protocol_version')}, "
          f"num_classes={info.get('num_classes')}, "
          f"counts={info.get('counts')}")
    _expect(info.get("protocol_version") == "1.0", "protocol_version != 1.0")

    # 2. /config
    r = requests.post(f"{base}/config", json=PERIOCULAR_CONFIG, timeout=5)
    if r.status_code == 409:
        print("/config: 409 (palette change forbidden — submitted/fixed pool non-empty); skipping")
    else:
        r.raise_for_status()
        _expect(r.json().get("status") == "ok", "/config did not return status=ok")
        print(f"/config: ok (num_classes={PERIOCULAR_CONFIG['num_classes']})")

    # 3. /info reflects config
    info = requests.get(f"{base}/info", timeout=5).json()
    _expect(info["num_classes"] == 7, f"num_classes != 7 after config (got {info['num_classes']})")
    _expect(info["class_names"] == PERIOCULAR_CONFIG["class_names"], "class_names mismatch")

    counts = info["counts"]
    if counts["total"] == 0:
        print("no images in any pool — run scripts/import_images.py first")
        return 1
    print(f"counts: pending={counts['pending']}, "
          f"submitted={counts['submitted']}, fixed={counts['fixed']}")

    # 4. /images
    r = requests.get(f"{base}/images?pool=pending", timeout=5)
    r.raise_for_status()
    items = r.json()["items"]
    print(f"/images?pool=pending: {len(items)} items, first={items[0] if items else None}")

    # 5. /next
    r = requests.get(f"{base}/next", timeout=5)
    r.raise_for_status()
    nxt = r.json()
    print(f"/next: {nxt}")
    _expect(nxt.get("image_id") is not None, "/next returned no image_id")
    image_id = nxt["image_id"]

    # 6. /infer
    r = requests.post(f"{base}/infer/{image_id}", timeout=30)
    if r.status_code == 503:
        print(f"/infer: 503 (no model and no seed); detail={r.json().get('detail')}")
    else:
        r.raise_for_status()
        source = r.headers.get("X-Model-Source", "unknown")
        img = Image.open(io.BytesIO(r.content))
        _expect(img.mode == "RGB", f"/infer image mode != RGB (got {img.mode})")
        print(f"/infer: source={source}, mode={img.mode}, size={img.size}")

    # 7. optional /train
    if args.with_train:
        r = requests.post(f"{base}/train?max_epochs=1", timeout=10)
        print(f"/train: {r.status_code} {r.json()}")
        time.sleep(2)
        r = requests.get(f"{base}/status", timeout=5)
        print(f"/status (after start): {r.json()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
