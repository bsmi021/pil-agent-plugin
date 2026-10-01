"""Tests for `blender_multiview_render.py`, moved from pil-agent-plugin's
`tests/test_multiview_render.py` and adapted to `blender_common`. The real
Blender render runs in `test_bi_blender_integration.py`."""

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_multiview_render  # noqa: E402
from blender_multiview_render import (  # noqa: E402
    ViewManifestError,
    build_probe_source,
    validate_view_manifest,
)


def _seven_views():
    return {
        "schema": "render-views-v1",
        "views": [
            {"name": "front", "direction": [0, -1, 0], "up": [0, 0, 1]},
            {"name": "front_right", "direction": [1, -1, 0], "up": [0, 0, 1]},
            {"name": "right", "direction": [1, 0, 0], "up": [0, 0, 1]},
            {"name": "back_right", "direction": [1, 1, 0], "up": [0, 0, 1]},
            {"name": "back", "direction": [0, 1, 0], "up": [0, 0, 1]},
            {"name": "back_left", "direction": [-1, 1, 0], "up": [0, 0, 1]},
            {"name": "front_left", "direction": [-1, -1, 0], "up": [0, 0, 1]},
        ],
    }


def test_seven_view_manifest_is_accepted_and_embedded_deterministically(tmp_path):
    views = validate_view_manifest(_seven_views())
    source = build_probe_source(views, tmp_path, 512, 512, 0.1, "analysis", True)

    assert len(views) == 7
    embedded = re.search(r"json\.loads\(r'''(.*?)'''\)", source, re.DOTALL)
    assert embedded is not None
    assert len(json.loads(embedded.group(1))["views"]) == 7
    assert "BVHTree" not in source


def test_parallel_direction_and_up_is_rejected():
    manifest = _seven_views()
    manifest["views"][0]["up"] = [0, -1, 0]

    with pytest.raises(ViewManifestError, match="parallel"):
        validate_view_manifest(manifest)


def test_a_blender_failure_is_a_one_line_refusal(tmp_path, monkeypatch, capsys):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"placeholder")
    manifest = tmp_path / "views.json"
    manifest.write_text(json.dumps(_seven_views()), encoding="utf-8")
    monkeypatch.setattr(
        blender_multiview_render.blender_common,
        "resolve_blender_executable",
        lambda _e: ("blender", None),
    )
    monkeypatch.setattr(
        blender_multiview_render.blender_common,
        "run_probe",
        lambda *_a, **_k: (None, "blender timed out after 300s"),
    )

    rc = blender_multiview_render.main([
        str(blend), "--manifest", str(manifest), "--output-dir", str(tmp_path / "out"),
    ])
    captured = capsys.readouterr()

    assert rc == 2
    assert captured.out == ""
    assert captured.err == "blender_multiview_render: blender timed out after 300s\n"


def test_a_render_the_probe_reports_but_did_not_write_is_refused(tmp_path, monkeypatch):
    missing = tmp_path / "front.png"
    monkeypatch.setattr(
        blender_multiview_render.blender_common,
        "run_probe",
        lambda *_a, **_k: ({"status": "RENDERED", "views": [{"name": "front", "path": str(missing)}]}, None),
    )

    with pytest.raises(ViewManifestError, match="missing render"):
        blender_multiview_render.render_manifest("blender", tmp_path / "s.blend", [], tmp_path)
