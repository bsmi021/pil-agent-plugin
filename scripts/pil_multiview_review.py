#!/usr/bin/env python
"""Aggregate an arbitrary named render/reference view set with worst-case verdicts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_VERSION = "0.7.0"


class ReviewManifestError(ValueError):
    pass


def _resolve(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.resolve().parent / path
    return path.resolve()


def build_pairs(manifest: dict, manifest_path: Path) -> list[dict]:
    if not isinstance(manifest, dict) or manifest.get("schema") != "review-views-v1":
        raise ReviewManifestError("manifest schema must be 'review-views-v1'")
    views = manifest.get("views")
    if not isinstance(views, list) or not views:
        raise ReviewManifestError("manifest requires a non-empty views array")
    names = set()
    pairs = []
    for view in views:
        name = view.get("name") if isinstance(view, dict) else None
        if not isinstance(name, str) or not name:
            raise ReviewManifestError("each view requires a non-empty name")
        if name in names:
            raise ReviewManifestError(f"duplicate view name: {name}")
        names.add(name)
        reference = _resolve(view.get("reference", ""), manifest_path)
        render = _resolve(view.get("render", ""), manifest_path)
        if not reference.is_file():
            raise ReviewManifestError(f"view {name!r} reference not found: {reference}")
        if not render.is_file():
            raise ReviewManifestError(f"view {name!r} render not found: {render}")
        pairs.append({"name": name, "a": str(reference), "b": str(render)})
    return pairs


def run_review(pairs: list[dict], contract: Path, thresholds: Path | None = None, timeout=300):
    script = Path(__file__).resolve().parent / "pil_contract_verdict.py"
    with tempfile.NamedTemporaryFile("w", suffix="_pil_multiview_pairs.json", delete=False, encoding="utf-8") as handle:
        json.dump([{"a": pair["a"], "b": pair["b"]} for pair in pairs], handle, sort_keys=True)
        manifest = Path(handle.name)
    cmd = [sys.executable, str(script), "--contract", str(contract), "--pairs", str(manifest), "--foreground"]
    if thresholds is not None:
        cmd.extend(["--thresholds", str(thresholds)])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        manifest.unlink(missing_ok=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:]
        raise ReviewManifestError(f"pil_contract_verdict exited {proc.returncode}: {tail[0] if tail else 'no diagnostic'}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ReviewManifestError(f"pil_contract_verdict emitted invalid JSON: {exc}") from exc


def _reject(reason):
    print(f"pil_multiview_review: {reason}", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review any number of named matched views as one worst-case contract.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--thresholds")
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest).resolve()
    contract = Path(args.contract).resolve()
    thresholds = Path(args.thresholds).resolve() if args.thresholds else None
    if not manifest_path.is_file():
        return _reject(f"manifest not found: {manifest_path}")
    if not contract.is_file():
        return _reject(f"contract not found: {contract}")
    if thresholds is not None and not thresholds.is_file():
        return _reject(f"thresholds not found: {thresholds}")
    try:
        pairs = build_pairs(json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path)
        verdict = run_review(pairs, contract, thresholds)
    except (OSError, ValueError, ReviewManifestError) as exc:
        return _reject(str(exc))
    payload = {
        "tool": "pil_multiview_review",
        "version": TOOL_VERSION,
        "parameters": {"manifest": str(manifest_path), "contract": str(contract), "view_count": len(pairs), "view_names": [pair["name"] for pair in pairs]},
        "views": pairs,
        "verdict": verdict,
        "interpretation_limits": [
            "The aggregate inherits pil_contract_verdict's worst-case rule: one violated or unmeasurable view cannot be averaged away.",
            "This tool compares rendered appearance and does not replace Blender BVH clearance or scene-geometry inspection.",
        ],
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
