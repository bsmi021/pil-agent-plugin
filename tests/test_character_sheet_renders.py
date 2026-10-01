"""`pil_character_sheet_review --renders`: the blender-inspect hand-off.

Pre-rendered views (saved `blender_render.py` or `blender_multiview_render.py`
payloads) replace the render step, so no Blender runs here. Hermetic: the
"renders" are small synthetic PNGs and the payloads are written in the shape
the producers emit. A view the manifest cannot supply takes the same
hard-fail sentinel path as a failed render, so it still counts in the
worst-case aggregate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_character_sheet_review as review  # noqa: E402

REVIEW_TOOL = SCRIPTS / "pil_character_sheet_review.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(REVIEW_TOOL), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
    )


def _render_png(path, box):
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(box, fill=(90, 110, 130, 255))
    image.save(path)
    return path


def _reference_png(path, box):
    image = Image.new("RGB", (64, 64), (230, 230, 230))
    ImageDraw.Draw(image).rectangle(box, fill=(90, 110, 130))
    image.save(path)
    return path


def _single_view_payload(path, view, output_path, rendered=True):
    render = {"rendered": rendered, "refused_reason": None if rendered else "scene has no render-visible mesh geometry"}
    render["output_path"] = str(output_path) if rendered else None
    payload = {
        "tool": "blender_render",
        "version": "0.1.0",
        "parameters": {"view": view},
        "render": render,
        "comparison": None,
        "interpretation_limits": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


@pytest.fixture
def sheet(tmp_path):
    """References and matching renders for front and side, plus a contract."""
    boxes = {"front": (16, 8, 47, 55), "side": (26, 8, 37, 55)}
    refs, renders = {}, {}
    for view, box in boxes.items():
        refs[view] = _reference_png(tmp_path / f"ref_{view}.png", box)
        renders[view] = _render_png(tmp_path / f"{view}.png", box)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"invariant": ["identity.silhouette_preserved"]}), encoding="utf-8")
    return tmp_path, refs, renders, contract


def _per_view_manifest(root, renders, views=None):
    entries = {}
    for view in views or renders:
        payload_path = root / f"{view}-render.json"
        _single_view_payload(payload_path, view, renders[view])
        entries[view] = payload_path.name
    manifest = root / "renders.json"
    manifest.write_text(json.dumps({"schema": "character-sheet-renders-v1", "views": entries}), encoding="utf-8")
    return manifest


def _multiview_manifest(root, renders, status="RENDERED"):
    payload = {
        "tool": "blender_multiview_render",
        "version": "0.1.0",
        "parameters": {},
        "render": {
            "status": status,
            "views": [
                {"name": view, "path": str(path), "direction": [0, -1, 0], "up": [0, 0, 1], "ortho_scale": 3.0}
                for view, path in renders.items()
            ] if status == "RENDERED" else [],
        },
        "interpretation_limits": [],
    }
    if status != "RENDERED":
        payload["render"]["reason"] = "scene has no render-visible mesh geometry"
    manifest = root / "multiview.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def _views(refs, *names):
    args = []
    for name in names:
        args += ["--view", f"{name}:{refs.get(name, refs['front'].parent / f'ref_{name}.png')}"]
    return args


# --- load_renders (unit) ------------------------------------------------------


def test_per_view_manifest_resolves_payload_paths_against_the_manifest(sheet):
    root, _refs, renders, _contract = sheet
    manifest = _per_view_manifest(root, renders)

    loaded = review.load_renders(manifest)

    assert set(loaded) == {"front", "side"}
    assert loaded["front"]["path"] == str(renders["front"])
    assert loaded["front"]["problem"] is None
    assert loaded["front"]["payload"]["tool"] == "blender_render"


def test_multiview_payload_splits_into_single_view_payloads(sheet):
    root, _refs, renders, _contract = sheet
    loaded = review.load_renders(_multiview_manifest(root, renders))

    assert loaded["side"]["path"] == str(renders["side"])
    assert [v["name"] for v in loaded["side"]["payload"]["render"]["views"]] == ["side"]
    assert loaded["side"]["payload"]["tool"] == "blender_multiview_render"


def test_a_blocked_multiview_render_is_a_problem_for_every_view(sheet):
    root, _refs, renders, _contract = sheet
    loaded = review.load_renders(_multiview_manifest(root, renders, status="RENDER_BLOCKED"))

    assert set(loaded) == {"front", "side", "back"}
    assert all(v["problem"].startswith("render blocked:") for v in loaded.values())


def test_a_refused_single_view_render_is_a_problem(sheet):
    root, _refs, renders, _contract = sheet
    payload_path = root / "front-render.json"
    _single_view_payload(payload_path, "front", renders["front"], rendered=False)
    manifest = root / "renders.json"
    manifest.write_text(json.dumps({"schema": "character-sheet-renders-v1", "views": {"front": str(payload_path)}}), encoding="utf-8")

    loaded = review.load_renders(manifest)

    assert loaded["front"]["path"] is None
    assert loaded["front"]["problem"].startswith("render refused: scene has no")


def test_a_missing_payload_file_is_a_problem_not_a_crash(sheet):
    root, _refs, _renders, _contract = sheet
    manifest = root / "renders.json"
    manifest.write_text(json.dumps({"schema": "character-sheet-renders-v1", "views": {"front": "absent.json"}}), encoding="utf-8")

    loaded = review.load_renders(manifest)

    assert "cannot read render payload" in loaded["front"]["problem"]


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"tool": "pil_image_info"}), json.dumps({"schema": "character-sheet-renders-v1", "views": ["front"]})],
    ids=["not-json", "array", "wrong-tool", "views-not-a-map"],
)
def test_an_unusable_manifest_is_a_manifest_error(tmp_path, content):
    manifest = tmp_path / "renders.json"
    manifest.write_text(content, encoding="utf-8")

    with pytest.raises(review.RendersManifestError):
        review.load_renders(manifest)


# --- end to end through pil_contract_verdict ----------------------------------


def test_prerendered_views_are_reviewed_without_blender(sheet):
    root, refs, renders, contract = sheet
    manifest = _per_view_manifest(root, renders)

    proc = _run("--renders", manifest, "--contract", contract, *_views(refs, "front", "side"))

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["parameters"]["blend"] is None
    assert payload["parameters"]["renders"] == str(manifest)
    assert payload["parameters"]["views"] == ["front", "side"]
    front = payload["per_view_renders"]["front"]
    assert front["hard_fail"] is None
    # Caller-owned renders keep their real paths; no render:// identifiers.
    assert front["manifest_pair"] == {"a": str(refs["front"]), "b": str(renders["front"])}
    assert front["render_payload"]["tool"] == "blender_render"
    assert len(payload["verdict"]["pairs"]) == 2
    assert "render://" not in proc.stdout


def test_a_view_missing_from_the_manifest_hard_fails_and_still_counts(sheet):
    root, refs, renders, contract = sheet
    manifest = _multiview_manifest(root, renders)
    back_ref = _reference_png(root / "ref_back.png", (16, 8, 47, 55))

    proc = _run("--renders", manifest, "--contract", contract,
                *_views(refs, "front", "side"), "--view", f"back:{back_ref}")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    back = payload["per_view_renders"]["back"]
    assert back["hard_fail"]["reason"].startswith("no pre-rendered image for view 'back'")
    assert back["manifest_pair"] == {"a": "hard-fail://back", "b": "hard-fail://back"}
    assert len(payload["verdict"]["pairs"]) == 3
    aggregate = payload["verdict"]["aggregate"][0]
    assert aggregate["verdict"] == "UNMEASURABLE"
    assert aggregate["pairs_unmeasurable"] >= 1


def test_a_deleted_render_image_hard_fails(sheet):
    root, refs, renders, contract = sheet
    manifest = _per_view_manifest(root, renders)
    renders["side"].unlink()

    proc = _run("--renders", manifest, "--contract", contract, *_views(refs, "front", "side"))

    assert proc.returncode == 0, proc.stderr
    side = json.loads(proc.stdout)["per_view_renders"]["side"]
    assert side["hard_fail"]["reason"].startswith("pre-rendered image not found")


def test_a_missing_reference_hard_fails_like_the_render_path(sheet):
    root, refs, renders, contract = sheet
    manifest = _per_view_manifest(root, renders)
    refs["side"].unlink()

    proc = _run("--renders", manifest, "--contract", contract, *_views(refs, "front", "side"))

    assert proc.returncode == 0, proc.stderr
    side = json.loads(proc.stdout)["per_view_renders"]["side"]
    assert side["hard_fail"]["reason"].startswith("reference file not found")


def test_two_runs_emit_byte_identical_json(sheet):
    root, refs, renders, contract = sheet
    manifest = _per_view_manifest(root, renders)
    args = ("--renders", manifest, "--contract", contract, *_views(refs, "front", "side"))

    first, second = _run(*args), _run(*args)

    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout


# --- rejections -----------------------------------------------------------------


@pytest.mark.parametrize("with_blend,with_renders", [(True, True), (False, False)], ids=["both", "neither"])
def test_exactly_one_of_blend_and_renders(sheet, with_blend, with_renders):
    root, refs, renders, contract = sheet
    args = []
    if with_blend:
        blend = root / "scene.blend"
        blend.write_bytes(b"")
        args.append(blend)
    if with_renders:
        args += ["--renders", _per_view_manifest(root, renders)]

    proc = _run(*args, "--contract", contract, *_views(refs, "front"))

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "either a .blend to render or --renders" in proc.stderr


def test_a_missing_or_malformed_renders_manifest_is_rejected(sheet):
    root, refs, _renders, contract = sheet
    missing = _run("--renders", root / "absent.json", "--contract", contract, *_views(refs, "front"))
    bad = root / "bad.json"
    bad.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
    malformed = _run("--renders", bad, "--contract", contract, *_views(refs, "front"))

    assert missing.returncode == 2 and missing.stdout == ""
    assert "renders manifest not found" in missing.stderr
    assert malformed.returncode == 2 and malformed.stdout == ""
    assert "is neither a blender_multiview_render payload" in malformed.stderr
    assert "Traceback" not in missing.stderr + malformed.stderr
