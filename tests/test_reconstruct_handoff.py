"""`pil_reconstruct` fit/render stages fed by externally produced payloads.

The blender-inspect hand-off: `blender_fit.py` and `blender_multiview_render.py`
run on their own, and the job points at their saved stdout with
`{"payload": PATH}`. No Blender runs here; prepare and solve run for real on
the calibrated fixture from `test_reconstruct.py`, and the review stage runs
`pil_multiview_review` (unchanged) on the external render manifest.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

import pil_reconstruct
from pil_reconstruct import ReconstructionError, run_job


def _silhouette(path, rgba):
    mode = "RGBA" if rgba else "RGB"
    background = (0, 0, 0, 0) if rgba else (230, 230, 230)
    raster = Image.new(mode, (64, 64), background)
    fill = (80, 100, 120, 255) if rgba else (80, 100, 120)
    ImageDraw.Draw(raster).polygon([(8, 55), (32, 5), (56, 55)], fill=fill)
    raster.save(path)
    return path


@pytest.fixture
def job_inputs(tmp_path):
    """The calibrated two-view solve fixture, as job input paths."""
    image = _silhouette(tmp_path / "reference.png", rgba=True)
    truth = np.asarray([[-1.0, 0.2, 0.0], [1.0, 0.2, 0.0], [0.0, 0.8, 2.0]])
    front = np.asarray([[1, 0, 0], [0, 0, 1]], dtype=float)
    right = np.asarray([[0, 1, 0], [0, 0, 1]], dtype=float)
    files = {
        "spec": {"schema": "multiview-spec-v1", "views": [{"name": "front", "image": str(image)}, {"name": "right", "image": str(image)}]},
        "template": {"schema": "template-mesh-v1", "vertices": (truth + 0.1).tolist(), "faces": [[0, 1, 2]]},
        "correspondences": {"schema": "correspondences-v1", "views": [
            {
                "name": name,
                "projection_matrix": projection.tolist(),
                "landmarks": [{"vertex": i, "target": t.tolist()} for i, t in enumerate(truth @ projection.T)],
            }
            for name, projection in (("front", front), ("right", right))
        ]},
        "constraints": {"schema": "geometry-constraints-v1", "template_weight": 0.0001, "edge_weight": 0.01},
    }
    job = {"schema": "reconstruction-job-v1"}
    for key, content in files.items():
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        job[key] = str(path)
    return tmp_path, job


def _fit_payload(root, status="PROBED", solution=None, output_path=None, tool="blender_fit"):
    fit = {"status": status}
    if status == "FIT_BLOCKED":
        fit["reason"] = "garment object not found or not a mesh"
    else:
        fit.update(before={"sample_count": 3}, after={"sample_count": 3}, output_path=output_path)
    payload = {
        "tool": tool,
        "version": "0.1.0",
        "parameters": {"blend": str(root / "scene.blend"), "solution": solution, "mode": "probe"},
        "fit": fit,
        "interpretation_limits": [],
    }
    path = root / "fit-payload.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _render_payload(root, status="RENDERED", tool="blender_multiview_render"):
    views = []
    if status == "RENDERED":
        for name in ("front", "right"):
            views.append({"name": name, "path": str(_silhouette(root / f"render-{name}.png", rgba=True)),
                          "direction": [0, -1, 0], "up": [0, 0, 1], "ortho_scale": 3.0})
    render = {"status": status, "views": views}
    if status != "RENDERED":
        render["reason"] = "scene has no render-visible mesh geometry"
    payload = {"tool": tool, "version": "0.1.0", "parameters": {}, "render": render, "interpretation_limits": []}
    path = root / "render-payload.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(job, root, name="output"):
    job_path = root / f"{name}-job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    return run_job(job, job_path, root / name)


def test_external_fit_and_render_payloads_complete_the_job_with_review(job_inputs):
    root, job = job_inputs
    references = {n: str(_silhouette(root / f"ref-{n}.png", rgba=False)) for n in ("front", "right")}
    contract = root / "contract.json"
    contract.write_text(json.dumps({"invariant": ["identity.silhouette_preserved"]}), encoding="utf-8")
    fit_path = _fit_payload(root)
    render_path = _render_payload(root)
    job.update(
        fit={"payload": fit_path.name},
        render={"payload": str(render_path)},
        review={"references": references, "contract": str(contract)},
    )

    payload = _run(job, root)

    assert payload["status"] == "COMPLETED"
    assert payload["stages"]["fit"] == json.loads(fit_path.read_text(encoding="utf-8"))
    assert payload["stages"]["render"] == json.loads(render_path.read_text(encoding="utf-8"))
    assert payload["artifacts"]["external_payloads"] == {"fit": str(fit_path), "render": str(render_path)}
    review = payload["stages"]["review"]
    assert review["tool"] == "pil_multiview_review"
    assert [view["b"] for view in review["views"]] == [
        str(root / "render-front.png"), str(root / "render-right.png"),
    ]


def test_without_external_stages_the_artifacts_name_none(job_inputs):
    root, job = job_inputs
    assert _run(job, root)["artifacts"]["external_payloads"] == {}


@pytest.mark.parametrize("tool", ["blender_fit", "pil_blender_fit"])
def test_an_external_fit_blocked_stops_at_fit_blocked(job_inputs, tool):
    root, job = job_inputs
    job["fit"] = {"payload": str(_fit_payload(root, status="FIT_BLOCKED", tool=tool))}
    job["render"] = {"payload": str(_render_payload(root))}

    payload = _run(job, root)

    assert payload["status"] == "FIT_BLOCKED"
    assert "render" not in payload["stages"]


@pytest.mark.parametrize("tool", ["blender_multiview_render", "pil_multiview_render"])
def test_an_external_blocked_render_is_render_blocked(job_inputs, tool):
    root, job = job_inputs
    job["render"] = {"payload": str(_render_payload(root, status="RENDER_BLOCKED", tool=tool))}

    assert _run(job, root)["status"] == "RENDER_BLOCKED"


def test_a_payload_from_the_wrong_tool_is_refused(job_inputs):
    root, job = job_inputs
    job["render"] = {"payload": str(_fit_payload(root))}

    with pytest.raises(ReconstructionError, match="must come from one of"):
        _run(job, root)


def test_a_missing_payload_file_is_refused(job_inputs):
    root, job = job_inputs
    job["fit"] = {"payload": "absent.json"}

    with pytest.raises(ReconstructionError, match="fit payload not found"):
        _run(job, root)


def test_an_external_fit_must_have_used_this_runs_solution(job_inputs):
    root, job = job_inputs
    first = _run(dict(job), root, name="first")
    same = first["artifacts"]["solution"]
    other = root / "other-solution.json"
    other.write_text(json.dumps({"status": "SOLVED", "vertices": [[0, 0, 0]] * 3}), encoding="utf-8")

    job["fit"] = {"payload": str(_fit_payload(root, solution=same))}
    assert _run(dict(job), root, name="same")["status"] == "COMPLETED"

    job["fit"] = {"payload": str(_fit_payload(root, solution=str(other)))}
    with pytest.raises(ReconstructionError, match="different solution"):
        _run(dict(job), root, name="other")


def test_an_in_process_render_after_an_external_fit_renders_the_fitted_copy(job_inputs, monkeypatch):
    root, job = job_inputs
    fitted = root / "fitted.blend"
    job["fit"] = {"payload": str(_fit_payload(root, status="FITTED", output_path=str(fitted)))}
    manifest = root / "views.json"
    manifest.write_text("{}", encoding="utf-8")
    job["render"] = {"blend": "unused.blend", "manifest": str(manifest)}
    real_run = pil_reconstruct._run
    calls = []

    def spy(script, arguments, timeout=600):
        if script == "pil_multiview_render.py":
            calls.append(arguments)
            return {"render": {"status": "RENDERED", "views": []}}
        return real_run(script, arguments, timeout)

    monkeypatch.setattr(pil_reconstruct, "_run", spy)
    payload = _run(job, root)

    assert payload["status"] == "COMPLETED"
    assert calls[0][0] == str(fitted)
