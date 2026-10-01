"""Tests for `blender_fit.py`, moved from pil-agent-plugin's
`tests/test_blender_fit.py` and adapted to `blender_common`. The real
Blender fit runs in `test_bi_blender_integration.py`."""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402
import blender_fit  # noqa: E402
from blender_fit import build_probe_source  # noqa: E402


def test_fit_probe_uses_blender_bvh_and_never_targets_source_path(tmp_path):
    source = tmp_path / "source.blend"
    output = tmp_path / "fitted.blend"

    script = build_probe_source(
        body_object="Body",
        garment_object="Cloak",
        clearance=0.01,
        max_displacement=0.05,
        mode="apply-copy",
        output_path=output,
        solution_path=None,
    )

    assert "BVHTree.FromObject" in script
    assert str(output).replace("\\", "\\\\") in script
    assert str(source) not in script


def test_fit_payload_parser_requires_exactly_one_sentinel_block():
    body = {"status": "PROBED", "minimum_signed_clearance": 0.02}
    begin, end = blender_common.sentinels("blender_fit")
    wrapped = f"noise\n{begin}\n" + json.dumps(body) + f"\n{end}\n"

    assert blender_common.extract_payload(wrapped, "blender_fit") == body
    assert blender_common.extract_payload(wrapped + wrapped, "blender_fit") is None


def test_a_probe_failure_is_a_one_line_refusal(tmp_path, monkeypatch, capsys):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"placeholder")
    monkeypatch.setattr(
        blender_fit.blender_common, "resolve_blender_executable", lambda _e: ("blender", None)
    )
    monkeypatch.setattr(
        blender_fit.blender_common, "run_probe", lambda *_a, **_k: (None, "blender exited 1: boom")
    )

    rc = blender_fit.main([str(blend), "--body-object", "Body", "--garment-object", "Cloak", "--clearance", "0.01"])
    captured = capsys.readouterr()

    assert rc == 2
    assert captured.out == ""
    assert captured.err == "blender_fit: blender exited 1: boom\n"


def test_apply_copy_refuses_to_overwrite_the_source(tmp_path, monkeypatch, capsys):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"placeholder")
    monkeypatch.setattr(
        blender_fit.blender_common, "resolve_blender_executable", lambda _e: ("blender", None)
    )

    rc = blender_fit.main([
        str(blend), "--body-object", "Body", "--garment-object", "Cloak",
        "--clearance", "0.01", "--mode", "apply-copy", "--output", str(blend),
    ])

    assert rc == 2
    assert capsys.readouterr().err == "blender_fit: output must not overwrite the input .blend\n"


def test_timeout_is_forwarded_but_not_echoed_in_parameters(tmp_path, monkeypatch, capsys):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"placeholder")
    seen = {}
    monkeypatch.setattr(
        blender_fit.blender_common, "resolve_blender_executable", lambda _e: ("blender", None)
    )

    def fake_probe(_blender, tool, _body, blend=None, timeout=None, **_kwargs):
        seen.update(tool=tool, timeout=timeout)
        return {"status": "PROBED"}, None

    monkeypatch.setattr(blender_fit.blender_common, "run_probe", fake_probe)
    rc = blender_fit.main([
        str(blend), "--body-object", "Body", "--garment-object", "Cloak",
        "--clearance", "0.01", "--timeout", "42",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert seen == {"tool": "blender_fit", "timeout": 42}
    assert payload["tool"] == "blender_fit"
    assert payload["fit"] == {"status": "PROBED"}
    assert set(payload["parameters"]) == {
        "blend", "body_object", "garment_object", "clearance",
        "max_displacement", "mode", "solution",
    }
