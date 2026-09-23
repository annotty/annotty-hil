#!/usr/bin/env python3
"""Annotty HIL protocol v1 conformance test (protocol/protocol.md).

Checks a running server against the response shapes the iPad client decodes
(AnnottyHIL/Services/HIL/HILServerClient.swift). Standard library only.

    python protocol/conformance_test.py BASE_URL [--api-key KEY] [--submit IMAGE_ID]

Read-only by default. --submit PUTs a blank (all-background) mask to IMAGE_ID,
which moves a pending image to the submitted pool.
Exit code 0 = all PASS, 1 = at least one FAIL.
"""
import argparse
import json
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib

# iPad palette (protocol §5.2): index 0 = background
IPAD_PALETTE = [
    [255, 255, 255], [255, 0, 0], [255, 128, 0], [255, 255, 0], [0, 255, 0],
    [0, 255, 255], [0, 0, 255], [128, 0, 255], [255, 102, 178],
]

# Required / optional fields per response (protocol §7). Types mirror the Swift
# Codable structs: "int" rejects bool/float, "number" accepts int or float.
INFO = {
    "required": {"name": "str", "protocol_version": "str", "num_classes": "int",
                 "class_names": "list[str]", "input_size": "int",
                 "counts": "obj", "model": "obj"},
}
COUNTS = {"required": {"pending": "int", "submitted": "int", "fixed": "int", "total": "int"}}
MODEL = {"required": {"best_exists": "bool", "coreml_exists": "bool", "version": "str",
                      "updated_at": "number", "md5": "str|null"}}
IMAGES = {"required": {"pool": "str", "count": "int", "items": "list[str]"}}
META = {"required": {"image_id": "str", "pool": "str", "has_seed": "bool",
                     "has_annotation": "bool", "bytes": "int", "width": "int", "height": "int"}}
NEXT = {"optional": {"image_id": "str", "pool": "str", "has_seed": "bool",
                     "has_annotation": "bool", "bytes": "int", "width": "int", "height": "int"}}
STATUS = {"required": {"state": "str"},
          "optional": {"epoch": "int", "max_epochs": "int", "best_metric": "number",
                       "metric_name": "str", "current_fold": "int", "n_folds": "int",
                       "started_at": "str", "completed_at": "str", "version": "str",
                       "error": "str"}}
CONFIG = {"required": {"status": "str"}, "optional": {"warning": "str"}}
SUBMIT = {"required": {"status": "str"}, "optional": {"image_id": "str", "pool": "str"}}
ERROR = {"required": {"detail": "str"}}


class Result:
    def __init__(self):
        self.failed = 0

    def check(self, name, problems):
        if problems:
            self.failed += 1
            for p in problems:
                print(f"FAIL  {name}: {p}")
        else:
            print(f"PASS  {name}")

    def skip(self, name, reason):
        print(f"SKIP  {name}: {reason}")


def type_ok(value, t):
    if t == "str":
        return isinstance(value, str)
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "bool":
        return isinstance(value, bool)
    if t == "obj":
        return isinstance(value, dict)
    if t == "list[str]":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if t == "str|null":
        return value is None or isinstance(value, str)
    raise ValueError(t)


def validate(obj, schema, where=""):
    """Return a list of problems; empty means the object decodes on the client."""
    if not isinstance(obj, dict):
        return [f"{where or 'body'} is not a JSON object"]
    problems = []
    for key, t in schema.get("required", {}).items():
        if key not in obj:
            problems.append(f"missing required key '{where}{key}'")
        elif not type_ok(obj[key], t):
            problems.append(f"'{where}{key}' must be {t}, got {json.dumps(obj[key])[:60]}")
    for key, t in schema.get("optional", {}).items():
        if obj.get(key) is not None and not type_ok(obj[key], t):
            problems.append(f"'{where}{key}' must be {t} or null, got {json.dumps(obj[key])[:60]}")
    return problems


class Client:
    def __init__(self, base, api_key):
        self.base = base.rstrip("/")
        self.api_key = api_key

    def request(self, method, path, body=None, headers=None, auth=True):
        req = urllib.request.Request(self.base + path, data=body, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if auth and self.api_key:
            req.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()


def parse_json(body):
    try:
        return json.loads(body), None
    except ValueError:
        return None, f"body is not JSON: {body[:80]!r}"


def png_size(data):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def blank_png(width, height):
    """All-white opaque RGBA PNG (= all background, protocol §5.1).
    RGBA on purpose: the iPad client sends RGBA, so servers must accept it."""
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
    raw = (b"\x00" + b"\xff" * (width * 4)) * height
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def check_json(res, name, status, body, schema, expected=(200,)):
    if status not in expected:
        res.check(name, [f"HTTP {status} (expected {expected}): {body[:120]!r}"])
        return None
    obj, err = parse_json(body)
    res.check(name, [err] if err else validate(obj, schema))
    return obj


def display_size(size):
    """Display resolution the server may pre-render masks at (protocol §9)."""
    w, h = size
    scale = min(2.0, 4096 / max(w, h))
    return int(w * scale), int(h * scale)


def check_mask(res, name, status, body, size, allowed_other):
    """200 must be a PNG at the image size or its display size (§5.1);
    `allowed_other` statuses need {"detail"}."""
    if status == 200:
        got = png_size(body)
        if got is None:
            res.check(name, ["200 body is not a PNG"])
        elif size and got not in (size, display_size(size)):
            ds = display_size(size)
            res.check(name, [f"PNG size {got[0]}x{got[1]} is neither the image size "
                             f"{size[0]}x{size[1]} nor the display size {ds[0]}x{ds[1]} (§5.1)"])
        else:
            res.check(name, [])
    elif status in allowed_other:
        obj, err = parse_json(body)
        res.check(f"{name} (HTTP {status})", [err] if err else validate(obj, ERROR))
    else:
        res.check(name, [f"unexpected HTTP {status}: {body[:120]!r}"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--submit", metavar="IMAGE_ID", help="also PUT /submit to this image (moves it to submitted)")
    args = ap.parse_args()

    c = Client(args.base_url, args.api_key.strip())
    res = Result()

    # §3 auth
    if c.api_key:
        status, _, body = c.request("GET", "/info", auth=False)
        if status != 401:
            res.check("auth: no key -> 401", [f"got HTTP {status}"])
        else:
            obj, err = parse_json(body)
            res.check("auth: no key -> 401", [err] if err else validate(obj, ERROR))
    else:
        res.skip("auth", "no --api-key given")

    # §7.1 /info
    status, _, body = c.request("GET", "/info")
    info = check_json(res, "GET /info", status, body, INFO)
    if info is None or not isinstance(info, dict):
        hint = " (pass --api-key if the server requires one)" if status == 401 and not c.api_key else ""
        print(f"\n/info failed; the client cannot connect. Fix it first.{hint}")
        return 1
    problems = []
    if isinstance(info.get("counts"), dict):
        problems += validate(info["counts"], COUNTS, "counts.")
    if isinstance(info.get("model"), dict):
        problems += validate(info["model"], MODEL, "model.")
    names, n = info.get("class_names"), info.get("num_classes")
    if isinstance(n, int) and n < 2:
        problems.append(f"num_classes must be >= 2 even before POST /config, got {n}")
    if isinstance(names, list) and isinstance(n, int) and len(names) != n:
        problems.append(f"len(class_names)={len(names)} != num_classes={n}")
    major = str(info.get("protocol_version", "")).split(".")[0]
    if major != "1":
        problems.append(f"protocol_version MAJOR must be 1, got {info.get('protocol_version')!r}")
    res.check("GET /info: counts / model / class definition", problems)

    # §7.2 /config — class definition from /info, colors from the iPad palette
    if isinstance(n, int) and 2 <= n <= len(IPAD_PALETTE) and isinstance(names, list):
        payload = json.dumps({"palette": IPAD_PALETTE[:n], "class_names": names, "num_classes": n}).encode()
        status, _, body = c.request("POST", "/config", payload, {"Content-Type": "application/json"})
        check_json(res, "POST /config", status, body, CONFIG)
    else:
        res.check("POST /config", [f"num_classes={n} is outside the iPad range 2..{len(IPAD_PALETTE)}"])

    # §7.3 /images
    first_id = None
    for pool in ("pending", "submitted", "fixed"):
        status, _, body = c.request("GET", f"/images?pool={pool}")
        env = check_json(res, f"GET /images?pool={pool}", status, body, IMAGES)
        if isinstance(env, dict) and isinstance(env.get("items"), list) and env["items"]:
            first_id = first_id or env["items"][0]
    # §7.9 /next
    status, _, body = c.request("GET", "/next")
    nxt = check_json(res, "GET /next", status, body, NEXT)
    if isinstance(nxt, dict):
        if "image_id" not in nxt:
            res.check("GET /next: image_id", ["'image_id' must be present (null when pending is empty)"])
        elif nxt["image_id"] is not None:
            res.check("GET /next: same shape as /meta", validate(nxt, META))

    # Fall back to /next so per-image checks still run when /images is malformed
    if first_id is None and isinstance(nxt, dict) and isinstance(nxt.get("image_id"), str):
        first_id = nxt["image_id"]
    if first_id is None:
        res.skip("per-image checks", "no images on the server")
    else:
        q = urllib.parse.quote(first_id)
        # §7.4 /meta
        status, _, body = c.request("GET", f"/images/{q}/meta")
        meta = check_json(res, f"GET /images/{first_id}/meta", status, body, META)
        size = None
        if isinstance(meta, dict) and type_ok(meta.get("width"), "int") and type_ok(meta.get("height"), "int"):
            size = (meta["width"], meta["height"])
        # §7.5 download
        status, headers, body = c.request("GET", f"/images/{q}/download")
        ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        res.check("GET /images/{id}/download",
                  [] if status == 200 and ctype.split(";")[0] in ("image/png", "image/jpeg")
                  else [f"HTTP {status}, Content-Type {ctype!r}"])
        # §7.6 labels, §7.7 infer (no query parameters)
        status, _, body = c.request("GET", f"/labels/{q}/download")
        check_mask(res, "GET /labels/{id}/download", status, body, size, (404,))
        status, _, body = c.request("POST", f"/infer/{q}")
        check_mask(res, "POST /infer/{id} (no query)", status, body, size, (503,))

    # §7.12 /status
    status, _, body = c.request("GET", "/status")
    check_json(res, "GET /status", status, body, STATUS)

    # §7.13 /models/latest (optional feature: 200 with headers, or 404)
    status, headers, body = c.request("GET", "/models/latest")
    if status == 200:
        lower = {k.lower() for k in headers}
        missing = [h for h in ("X-Model-Version", "X-Model-Md5", "X-Model-Updated-At") if h.lower() not in lower]
        res.check("GET /models/latest", [f"missing header {h}" for h in missing])
    elif status == 404:
        obj, err = parse_json(body)
        res.check("GET /models/latest (404)", [err] if err else validate(obj, ERROR))
    else:
        res.check("GET /models/latest", [f"expected 200 or 404, got HTTP {status}"])

    # §7.8 /submit (opt-in, moves the image)
    if args.submit:
        q = urllib.parse.quote(args.submit)
        status, _, body = c.request("GET", f"/images/{q}/meta")
        meta, _ = parse_json(body) if status == 200 else (None, None)
        if not isinstance(meta, dict) or not type_ok(meta.get("width"), "int") or not type_ok(meta.get("height"), "int"):
            res.check("PUT /submit/{id}", [f"cannot read size of {args.submit} from /meta (HTTP {status})"])
        else:
            boundary = uuid.uuid4().hex
            png = blank_png(meta["width"], meta["height"])
            form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"mask.png\"\r\n"
                    f"Content-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{boundary}--\r\n".encode()
            status, _, body = c.request("PUT", f"/submit/{q}", form,
                                        {"Content-Type": f"multipart/form-data; boundary={boundary}"})
            check_json(res, "PUT /submit/{id}", status, body, SUBMIT)
    else:
        res.skip("PUT /submit/{id}", "pass --submit IMAGE_ID to test (moves the image to submitted)")

    print(f"\n{'ALL PASS' if res.failed == 0 else f'{res.failed} FAIL'}")
    return 0 if res.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
