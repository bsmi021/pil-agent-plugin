import json

import numpy as np
from PIL import Image, ImageDraw
from pil_reconstruct import run_job, terminal_status


def test_pipeline_refusal_statuses_are_not_flattened_to_completed():
    assert terminal_status({"solve": {"status": "UNDERDETERMINED"}}) == "UNDERDETERMINED"
    assert terminal_status({"solve": {"status": "VIEW_CONFLICT"}}) == "VIEW_CONFLICT"
    assert terminal_status({"solve": {"status": "SOLVED"}, "fit": {"fit": {"status": "FIT_BLOCKED"}}}) == "FIT_BLOCKED"
    assert terminal_status({"solve": {"status": "SOLVED"}, "render": {"render": {"status": "RENDER_BLOCKED"}}}) == "RENDER_BLOCKED"


def test_pipeline_reports_completed_only_after_non_blocking_stages():
    stages = {
        "solve": {"status": "SOLVED"},
        "fit": {"fit": {"status": "PROBED"}},
        "render": {"render": {"status": "RENDERED"}},
    }
    assert terminal_status(stages) == "COMPLETED"


def test_prepare_and_solve_orchestration_completes_on_a_calibrated_fixture(tmp_path):
    image = tmp_path / "reference.png"
    raster = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(raster).polygon([(8, 55), (32, 5), (56, 55)], fill=(80, 100, 120, 255))
    raster.save(image)
    truth = np.asarray([[-1.0, 0.2, 0.0], [1.0, 0.2, 0.0], [0.0, 0.8, 2.0]])
    front = np.asarray([[1, 0, 0], [0, 0, 1]], dtype=float)
    right = np.asarray([[0, 1, 0], [0, 0, 1]], dtype=float)

    spec = tmp_path / "spec.json"
    template = tmp_path / "template.json"
    correspondences = tmp_path / "correspondences.json"
    constraints = tmp_path / "constraints.json"
    spec.write_text(json.dumps({
        "schema": "multiview-spec-v1",
        "views": [{"name": "front", "image": str(image)}, {"name": "right", "image": str(image)}],
    }), encoding="utf-8")
    template.write_text(json.dumps({
        "schema": "template-mesh-v1",
        "vertices": (truth + 0.1).tolist(),
        "faces": [[0, 1, 2]],
    }), encoding="utf-8")
    correspondence_views = []
    for name, projection in (("front", front), ("right", right)):
        correspondence_views.append({
            "name": name,
            "projection_matrix": projection.tolist(),
            "landmarks": [
                {"vertex": index, "target": target.tolist()}
                for index, target in enumerate(truth @ projection.T)
            ],
        })
    correspondences.write_text(json.dumps({"schema": "correspondences-v1", "views": correspondence_views}), encoding="utf-8")
    constraints.write_text(json.dumps({
        "schema": "geometry-constraints-v1",
        "template_weight": 0.0001,
        "edge_weight": 0.01,
    }), encoding="utf-8")
    job_path = tmp_path / "job.json"
    job = {
        "schema": "reconstruction-job-v1",
        "spec": str(spec),
        "template": str(template),
        "correspondences": str(correspondences),
        "constraints": str(constraints),
    }
    job_path.write_text(json.dumps(job), encoding="utf-8")

    payload = run_job(job, job_path, tmp_path / "output")

    assert payload["status"] == "COMPLETED"
    assert payload["stages"]["prepare"]["status"] == "PREPARED"
    assert payload["stages"]["solve"]["status"] == "SOLVED"
    assert payload["stages"]["solve"]["residuals"]["landmark_rmse"] < 1e-3
