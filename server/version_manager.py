"""Track the current model version and produce the v1.0 ``X-Model-*`` headers.

``data/models/version.json`` stores the monotonically-increasing version
counter together with a snapshot of when ``bump_version()`` was last
called and the MD5 of the published artefact (CoreML ``Manifest.json``
when available, otherwise the zipped package).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import config


_VERSION_PATH = config.MODELS_DIR / "version.json"


def _hash_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _coreml_md5() -> str | None:
    """MD5 of CoreML Manifest.json (preferred) or the package zip."""
    manifest = config.COREML_PATH / "Manifest.json"
    if manifest.exists():
        return _hash_file(manifest)
    if config.COREML_ZIP_PATH.exists():
        return _hash_file(config.COREML_ZIP_PATH)
    return None


def current_version() -> dict:
    """Return ``{"version": str, "updated_at": float, "md5": str|None}``."""
    if not _VERSION_PATH.exists():
        return {"version": "0", "updated_at": 0.0, "md5": None}
    try:
        data = json.loads(_VERSION_PATH.read_text(encoding="utf-8"))
        return {
            "version": str(data.get("version", "0")),
            "updated_at": float(data.get("updated_at", 0.0)),
            "md5": data.get("md5"),
        }
    except Exception:  # noqa: BLE001
        return {"version": "0", "updated_at": 0.0, "md5": None}


def bump_version() -> dict:
    """Increment the counter and refresh the MD5 + timestamp."""
    cur = current_version()
    try:
        next_v = str(int(cur["version"]) + 1)
    except (TypeError, ValueError):
        next_v = "1"
    info = {
        "version": next_v,
        "updated_at": time.time(),
        "md5": _coreml_md5(),
    }
    _VERSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _VERSION_PATH.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def model_info() -> dict:
    """Build the ``model`` block of ``GET /info`` per protocol v1 §7.1."""
    cur = current_version()
    return {
        "best_exists": config.BEST_MODEL_PATH.exists(),
        "coreml_exists": config.COREML_PATH.exists() or config.COREML_ZIP_PATH.exists(),
        "version": cur["version"],
        "updated_at": cur["updated_at"],
        "md5": cur["md5"],
    }


def latest_headers() -> dict:
    """Return the v1.0 ``X-Model-*`` headers for ``GET /models/latest``."""
    info = current_version()
    return {
        "X-Model-Version": str(info["version"]),
        "X-Model-Md5": str(info["md5"] or ""),
        "X-Model-Updated-At": str(info["updated_at"]),
    }
