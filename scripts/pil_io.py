"""Shared strict JSON, hashes and non-overwriting artifact writes."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_constant=lambda x: (_ for _ in ()).throw(
            ValueError(f"nonfinite JSON: {x}")
        ),
    )


def write_json(path, payload):
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(text)


def save_png(path, image):
    with Path(path).open("xb") as stream:
        image.save(stream, format="PNG")


def positive(value, name, zero=False):
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(
            f"{name} must be finite and {'nonnegative' if zero else 'positive'}"
        )
    return value


def emit(payload):
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
