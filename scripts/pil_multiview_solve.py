#!/usr/bin/env python
"""Fit template vertices to calibrated multi-view constraints with SciPy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

TOOL_VERSION = "0.7.0"


class SolveError(ValueError):
    pass


def _scipy():
    try:
        from scipy.optimize import least_squares
        from scipy.spatial import cKDTree
    except ImportError as exc:  # pragma: no cover
        raise SolveError("SciPy is required; install the plugin's reconstruction extra") from exc
    return least_squares, cKDTree


def _vertices(template: dict) -> np.ndarray:
    if template.get("schema") != "template-mesh-v1":
        raise SolveError("template schema must be 'template-mesh-v1'")
    vertices = np.asarray(template.get("vertices"), dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) == 0:
        raise SolveError("template vertices must be a non-empty Nx3 array")
    if not np.isfinite(vertices).all():
        raise SolveError("template vertices must be finite")
    return vertices


def _projection(view: dict) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.asarray(view.get("projection_matrix"), dtype=float)
    offset = np.asarray(view.get("offset", [0.0, 0.0]), dtype=float)
    if matrix.shape != (2, 3) or offset.shape != (2,):
        raise SolveError(f"view {view.get('name')!r} requires a 2x3 projection_matrix and 2-vector offset")
    return matrix, offset


def _constraint_rank(vertex_count: int, views: list[dict], fixed: list[dict]) -> tuple[int, int]:
    rows = []
    for view in views:
        matrix, _offset = _projection(view)
        for landmark in view.get("landmarks", []):
            index = int(landmark["vertex"])
            if index < 0 or index >= vertex_count:
                raise SolveError(f"landmark vertex index out of range: {index}")
            for projection_row in matrix:
                row = np.zeros(vertex_count * 3, dtype=float)
                row[index * 3:index * 3 + 3] = projection_row
                rows.append(row)
    for item in fixed:
        index = int(item["vertex"])
        if index < 0 or index >= vertex_count:
            raise SolveError(f"fixed vertex index out of range: {index}")
        for axis in range(3):
            row = np.zeros(vertex_count * 3, dtype=float)
            row[index * 3 + axis] = 1.0
            rows.append(row)
    matrix = np.vstack(rows) if rows else np.empty((0, vertex_count * 3))
    rank = int(np.linalg.matrix_rank(matrix))
    return rank, vertex_count * 3


def solve_reconstruction(template: dict, correspondences: dict, constraints: dict | None = None, prepared: dict | None = None) -> dict:
    least_squares, cKDTree = _scipy()
    initial = _vertices(template)
    if correspondences.get("schema") != "correspondences-v1":
        raise SolveError("correspondences schema must be 'correspondences-v1'")
    views = correspondences.get("views")
    if not isinstance(views, list) or not views:
        raise SolveError("correspondences requires a non-empty views array")
    constraints = constraints or {"schema": "geometry-constraints-v1"}
    if constraints.get("schema") != "geometry-constraints-v1":
        raise SolveError("constraints schema must be 'geometry-constraints-v1'")
    fixed = constraints.get("fixed_vertices", [])
    rank, variables = _constraint_rank(len(initial), views, fixed)
    rank_block = {"rank": rank, "variables": variables, "deficiency": variables - rank}
    if rank < variables:
        return {
            "tool": "pil_multiview_solve",
            "version": TOOL_VERSION,
            "schema": "reconstruction-result-v1",
            "status": "UNDERDETERMINED",
            "rank": rank_block,
            "vertices": None,
            "residuals": None,
            "reason": "independent 2D observations and fixed coordinates do not constrain every 3D vertex coordinate",
        }

    edges = set()
    for face in template.get("faces", []):
        for a, b in zip(face, face[1:] + face[:1]):
            edges.add(tuple(sorted((int(a), int(b)))))
    edge_weight = float(constraints.get("edge_weight", 0.0))
    template_weight = float(constraints.get("template_weight", 0.0))
    symmetry = constraints.get("symmetry_pairs", [])
    symmetry_weight = float(constraints.get("symmetry_weight", 1.0))

    contour_by_name = {}
    if prepared is not None:
        for view in prepared.get("views", []):
            contour = view.get("contour_normalized")
            if contour:
                contour_by_name[view["name"]] = cKDTree(np.asarray(contour, dtype=float))
    for view in views:
        if view.get("silhouette_vertices") and view.get("name") not in contour_by_name:
            raise SolveError(
                f"view {view.get('name')!r} declares silhouette_vertices but has no measured prepared contour"
            )

    landmark_slices = []

    def residual_vector(flat):
        vertices = flat.reshape((-1, 3))
        residuals = []
        landmark_slices.clear()
        for view in views:
            matrix, offset = _projection(view)
            for landmark in view.get("landmarks", []):
                index = int(landmark["vertex"])
                target = np.asarray(landmark["target"], dtype=float)
                weight = float(landmark.get("weight", 1.0))
                start = len(residuals)
                residuals.extend(((matrix @ vertices[index] + offset - target) * weight).tolist())
                landmark_slices.append(slice(start, start + 2))
            tree = contour_by_name.get(view.get("name"))
            for index in view.get("silhouette_vertices", []):
                projected = matrix @ vertices[int(index)] + offset
                distance, _nearest = tree.query(projected) if tree is not None else (0.0, None)
                residuals.append(float(distance) * float(view.get("silhouette_weight", 1.0)))
        for item in fixed:
            index = int(item["vertex"])
            target = np.asarray(item["position"], dtype=float)
            residuals.extend(((vertices[index] - target) * float(item.get("weight", 1.0))).tolist())
        if template_weight > 0:
            residuals.extend(((vertices - initial) * template_weight).ravel().tolist())
        if edge_weight > 0:
            for a, b in sorted(edges):
                residuals.extend((((vertices[b] - vertices[a]) - (initial[b] - initial[a])) * edge_weight).tolist())
        for pair in symmetry:
            left = vertices[int(pair["left"])]
            right = vertices[int(pair["right"])]
            axis = {"x": 0, "y": 1, "z": 2}[pair.get("axis", "x")]
            center = float(pair.get("center", 0.0))
            mirrored = right.copy()
            mirrored[axis] = 2.0 * center - mirrored[axis]
            residuals.extend(((left - mirrored) * symmetry_weight * float(pair.get("weight", 1.0))).tolist())
        return np.asarray(residuals, dtype=float)

    result = least_squares(
        residual_vector,
        initial.ravel(),
        method="trf",
        max_nfev=int(constraints.get("max_nfev", 1000)),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
    )
    solved = result.x.reshape((-1, 3))
    landmark_errors = []
    per_view = {}
    for view in views:
        matrix, offset = _projection(view)
        errors = []
        for landmark in view.get("landmarks", []):
            predicted = matrix @ solved[int(landmark["vertex"])] + offset
            errors.append(float(np.linalg.norm(predicted - np.asarray(landmark["target"], dtype=float))))
        per_view[view.get("name", "unnamed")] = {
            "landmark_count": len(errors),
            "landmark_rmse": round(float(np.sqrt(np.mean(np.square(errors)))) if errors else 0.0, 8),
            "landmark_max": round(max(errors) if errors else 0.0, 8),
        }
        landmark_errors.extend(errors)
    landmark_rmse = float(np.sqrt(np.mean(np.square(landmark_errors)))) if landmark_errors else 0.0
    limit = float(constraints.get("max_landmark_rmse", 0.01))
    status = "SOLVED" if result.success and landmark_rmse <= limit else "VIEW_CONFLICT"
    return {
        "tool": "pil_multiview_solve",
        "version": TOOL_VERSION,
        "schema": "reconstruction-result-v1",
        "status": status,
        "rank": rank_block,
        "vertices": [[round(float(value), 8) for value in vertex] for vertex in solved],
        "residuals": {
            "landmark_rmse": round(landmark_rmse, 8),
            "max_landmark_rmse": limit,
            "per_view": per_view,
            "optimizer_cost": round(float(result.cost), 10),
            "optimizer_optimality": round(float(result.optimality), 10),
        },
        "optimizer": {"success": bool(result.success), "message": str(result.message), "evaluations": int(result.nfev)},
        "interpretation_limits": [
            "A solved vertex set is conditional on caller-supplied camera projections, landmark correspondences, and metric anchors.",
            "Regularization stabilizes a constrained solution but is not counted as independent observation rank.",
            "Image contours constrain projection only; they do not prove hidden topology or cloth simulation behavior.",
        ],
    }


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _reject(reason: str) -> int:
    print(f"pil_multiview_solve: {reason}", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fit a template mesh to calibrated multi-view constraints.")
    parser.add_argument("--template", required=True)
    parser.add_argument("--correspondences", required=True)
    parser.add_argument("--constraints")
    parser.add_argument("--prepared")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        payload = solve_reconstruction(
            _load(args.template),
            _load(args.correspondences),
            _load(args.constraints) if args.constraints else None,
            _load(args.prepared) if args.prepared else None,
        )
    except (OSError, ValueError, SolveError) as exc:
        return _reject(str(exc))
    encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
