import numpy as np
from pil_multiview_solve import solve_reconstruction


def _problem(include_side=True):
    truth = np.array([
        [-1.0, 0.2, 0.0],
        [1.0, 0.2, 0.0],
        [0.0, 0.8, 2.0],
    ])
    template = {
        "schema": "template-mesh-v1",
        "vertices": (truth + np.array([0.15, -0.1, 0.12])).tolist(),
        "faces": [[0, 1, 2]],
    }
    front = [[1, 0, 0], [0, 0, 1]]
    side = [[0, 1, 0], [0, 0, 1]]
    views = [{"name": "front", "projection_matrix": front, "offset": [0, 0]}]
    if include_side:
        views.append({"name": "right", "projection_matrix": side, "offset": [0, 0]})
    correspondences = {
        "schema": "correspondences-v1",
        "views": [],
    }
    for view in views:
        matrix = np.asarray(view["projection_matrix"], dtype=float)
        targets = truth @ matrix.T
        correspondences["views"].append({
            **view,
            "landmarks": [
                {"vertex": index, "target": target.tolist(), "weight": 1.0}
                for index, target in enumerate(targets)
            ],
        })
    constraints = {
        "schema": "geometry-constraints-v1",
        "template_weight": 0.0001,
        "edge_weight": 0.01,
    }
    return truth, template, correspondences, constraints


def test_two_orthographic_views_recover_three_dimensional_vertices():
    truth, template, correspondences, constraints = _problem(include_side=True)

    payload = solve_reconstruction(template, correspondences, constraints)

    assert payload["status"] == "SOLVED"
    assert payload["rank"]["deficiency"] == 0
    assert np.allclose(np.asarray(payload["vertices"]), truth, atol=2e-3)
    assert payload["residuals"]["landmark_rmse"] < 1e-3


def test_single_view_refuses_underdetermined_depth():
    _truth, template, correspondences, constraints = _problem(include_side=False)

    payload = solve_reconstruction(template, correspondences, constraints)

    assert payload["status"] == "UNDERDETERMINED"
    assert payload["rank"]["deficiency"] > 0
    assert payload["vertices"] is None


def test_conflicting_locked_observations_report_view_conflict():
    _truth, template, correspondences, constraints = _problem(include_side=True)
    correspondences["views"][1]["landmarks"][0]["target"][1] += 10.0
    constraints["fixed_vertices"] = [{"vertex": 0, "position": [-1.0, 0.2, 0.0], "weight": 100.0}]
    constraints["max_landmark_rmse"] = 0.01

    payload = solve_reconstruction(template, correspondences, constraints)

    assert payload["status"] == "VIEW_CONFLICT"
    assert payload["residuals"]["landmark_rmse"] > 0.01
