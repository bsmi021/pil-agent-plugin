#!/usr/bin/env python
"""Orchestrate preparation, solving, optional BVH fitting, rendering, and review.

Hand-off input. The ``fit`` and ``render`` job stages may each be
``{"payload": PATH}`` instead of instructions: PATH is the saved stdout of
blender-inspect's ``blender_fit.py`` or ``blender_multiview_render.py`` (the
deprecated ``pil_blender_fit``/``pil_multiview_render`` payloads are accepted
too), and it is used as that stage's result without starting Blender. An
external fit must have used this run's own solution when it names one. When
the fit is external but the render is not, the render stage renders the fit's
output .blend (apply-copy) or its input .blend (probe), as an in-process fit
would.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TOOL_VERSION = "0.9.6"
SCRIPTS = Path(__file__).resolve().parent


class ReconstructionError(ValueError):
    pass


FIT_TOOLS = frozenset({"blender_fit", "pil_blender_fit"})
RENDER_TOOLS = frozenset({"blender_multiview_render", "pil_multiview_render"})


def _external_payload(stage: dict, name: str, tools: frozenset, key: str, job_path: Path) -> tuple[dict, Path]:
    """Load and check an externally produced stage payload."""
    path = _resolve(stage["payload"], job_path)
    if not path.is_file():
        raise ReconstructionError(f"{name} payload not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReconstructionError(f"{name} payload is not JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("tool") not in tools:
        raise ReconstructionError(f"{name} payload must come from one of {sorted(tools)}: {path}")
    if not isinstance(payload.get(key), dict) or not isinstance(payload[key].get("status"), str):
        raise ReconstructionError(f"{name} payload has no {key}.status: {path}")
    return payload, path


def _check_fit_solution(fit_payload: dict, solution_path: Path) -> None:
    """An external fit that names a solution must have used this run's one."""
    used = (fit_payload.get("parameters") or {}).get("solution")
    if not used:
        return
    try:
        used_vertices = json.loads(Path(used).read_text(encoding="utf-8")).get("vertices")
    except (OSError, ValueError) as exc:
        raise ReconstructionError(f"cannot read the external fit's solution {used}: {exc}") from exc
    ours = json.loads(solution_path.read_text(encoding="utf-8")).get("vertices")
    if used_vertices != ours:
        raise ReconstructionError(f"external fit used a different solution than this run: {used}")


def _resolve(value: str, job_path: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = job_path.resolve().parent / path
    return path.resolve()


def _run(script: str, arguments: list[str], timeout=600) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:]
        raise ReconstructionError(f"{script} exited {proc.returncode}: {tail[0] if tail else 'no diagnostic'}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ReconstructionError(f"{script} emitted invalid JSON: {exc}") from exc


def terminal_status(stages: dict) -> str:
    solve = stages.get("solve", {}).get("status")
    if solve in {"UNDERDETERMINED", "VIEW_CONFLICT"}:
        return solve
    fit = stages.get("fit", {}).get("fit", {}).get("status")
    if fit == "FIT_BLOCKED":
        return "FIT_BLOCKED"
    render = stages.get("render", {}).get("render", {}).get("status")
    if render == "RENDER_BLOCKED":
        return "RENDER_BLOCKED"
    return "COMPLETED"


def run_job(job: dict, job_path: Path, output_dir: Path) -> dict:
    if job.get("schema") != "reconstruction-job-v1":
        raise ReconstructionError("job schema must be 'reconstruction-job-v1'")
    for key in ("spec", "template", "correspondences", "constraints"):
        if not isinstance(job.get(key), str):
            raise ReconstructionError(f"job requires {key!r} path")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ReconstructionError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    spec = _resolve(job["spec"], job_path)
    template = _resolve(job["template"], job_path)
    correspondences = _resolve(job["correspondences"], job_path)
    constraints = _resolve(job["constraints"], job_path)
    for path in (spec, template, correspondences, constraints):
        if not path.is_file():
            raise ReconstructionError(f"input not found: {path}")

    stages = {}
    stages["prepare"] = _run("pil_multiview_prepare.py", [str(spec)])
    prepared_path = output_dir / "prepared.json"
    prepared_path.write_text(json.dumps(stages["prepare"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    solution_path = output_dir / "solution.json"
    stages["solve"] = _run(
        "pil_multiview_solve.py",
        ["--template", str(template), "--correspondences", str(correspondences), "--constraints", str(constraints), "--prepared", str(prepared_path), "--output", str(solution_path)],
    )
    if stages["solve"].get("status") != "SOLVED":
        return {"tool": "pil_reconstruct", "version": TOOL_VERSION, "status": terminal_status(stages), "stages": stages}

    active_blend = None
    external = {}
    fit = job.get("fit")
    if fit is not None and "payload" in fit:
        stages["fit"], external["fit"] = _external_payload(fit, "fit", FIT_TOOLS, "fit", job_path)
        _check_fit_solution(stages["fit"], solution_path)
        if stages["fit"]["fit"]["status"] == "FIT_BLOCKED":
            return {"tool": "pil_reconstruct", "version": TOOL_VERSION, "status": terminal_status(stages), "stages": stages}
        fitted = stages["fit"]["fit"].get("output_path")
        source = (stages["fit"].get("parameters") or {}).get("blend")
        active_blend = Path(fitted) if fitted else (Path(source) if source else None)
    elif fit is not None:
        blend = _resolve(fit["blend"], job_path)
        active_blend = output_dir / "fitted.blend" if fit.get("mode", "probe") == "apply-copy" else blend
        args = [
            str(blend),
            "--body-object", fit["body_object"],
            "--garment-object", fit["garment_object"],
            "--clearance", str(fit["clearance"]),
            "--max-displacement", str(fit.get("max_displacement", 0.05)),
            "--mode", fit.get("mode", "probe"),
            "--solution", str(solution_path),
        ]
        if fit.get("mode", "probe") == "apply-copy":
            args.extend(["--output", str(active_blend)])
        if fit.get("blender_executable"):
            args.extend(["--blender-executable", fit["blender_executable"]])
        stages["fit"] = _run("pil_blender_fit.py", args)
        if stages["fit"].get("fit", {}).get("status") == "FIT_BLOCKED":
            return {"tool": "pil_reconstruct", "version": TOOL_VERSION, "status": terminal_status(stages), "stages": stages}

    render = job.get("render")
    if render is not None and "payload" in render:
        stages["render"], external["render"] = _external_payload(render, "render", RENDER_TOOLS, "render", job_path)
    elif render is not None:
        render_blend = active_blend or _resolve(render["blend"], job_path)
        render_dir = output_dir / "renders"
        args = [
            str(render_blend),
            "--manifest", str(_resolve(render["manifest"], job_path)),
            "--output-dir", str(render_dir),
            "--width", str(render.get("width", 1024)),
            "--height", str(render.get("height", 1024)),
            "--margin", str(render.get("margin", 0.1)),
            "--mode", render.get("mode", "analysis"),
        ]
        if render.get("blender_executable"):
            args.extend(["--blender-executable", render["blender_executable"]])
        stages["render"] = _run("pil_multiview_render.py", args)

    review = job.get("review")
    if review is not None:
        if "render" not in stages or stages["render"].get("render", {}).get("status") != "RENDERED":
            raise ReconstructionError("review requires a successful render stage")
        references = review.get("references", {})
        review_views = []
        for rendered in stages["render"]["render"]["views"]:
            name = rendered["name"]
            if name not in references:
                raise ReconstructionError(f"review reference missing for rendered view {name!r}")
            review_views.append({"name": name, "reference": str(_resolve(references[name], job_path)), "render": rendered["path"]})
        review_manifest = output_dir / "review-manifest.json"
        review_manifest.write_text(json.dumps({"schema": "review-views-v1", "views": review_views}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        args = ["--manifest", str(review_manifest), "--contract", str(_resolve(review["contract"], job_path))]
        if review.get("thresholds"):
            args.extend(["--thresholds", str(_resolve(review["thresholds"], job_path))])
        stages["review"] = _run("pil_multiview_review.py", args)

    return {
        "tool": "pil_reconstruct",
        "version": TOOL_VERSION,
        "status": terminal_status(stages),
        "stages": stages,
        "artifacts": {
            "prepared": str(prepared_path),
            "solution": str(solution_path),
            "output_dir": str(output_dir),
            "external_payloads": {stage: str(path) for stage, path in sorted(external.items())},
        },
    }


def _reject(reason):
    print(f"pil_reconstruct: {reason}", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the multiview reconstruction pipeline with explicit refusal states.")
    parser.add_argument("job")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    job_path = Path(args.job).resolve()
    if not job_path.is_file():
        return _reject(f"job not found: {job_path}")
    try:
        payload = run_job(json.loads(job_path.read_text(encoding="utf-8")), job_path, Path(args.output_dir).resolve())
    except (OSError, ValueError, subprocess.TimeoutExpired, ReconstructionError) as exc:
        return _reject(str(exc))
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
