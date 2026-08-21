#!/usr/bin/env python
"""Character-sheet review: compose matched-view renders into one contract verdict.

For each `--view NAME:REF_PATH` (repeatable, NAME in {front, side, back}),
invoke ``pil_blender_render.py`` as a subprocess with ``--reference REF_PATH``
to render that view against its reference. Every resulting (reference, render)
pair is then handed to ``pil_contract_verdict.py --pairs`` as a single manifest
so a whole character sheet is one contract evaluated over N registered pairs.

    python pil_character_sheet_review.py scene.blend \\
        --contract contract.json \\
        --view front:refs/front.png \\
        --view side:refs/side.png \\
        --view back:refs/back.png

Pair convention: manifest entries are ``{"a": REFERENCE, "b": RENDER}``. This
matches ``pil_blender_render``'s own ``run_structure_diff`` call which passes
the reference as ``a`` and the fresh render as ``b``, so directional palette
and structure fields (``hue_families_gained``, ``hue_families_lost``, etc.)
read consistently as "render minus reference" across both tools. State this
in ``interpretation_limits`` so a caller reading a per-pair block does not
have to guess which side is under test.

Refusal-carrying vs. hard-failed renders are handled distinctly, and both
still count in the aggregate:

*   ``comparison.refused = true`` (empty/degenerate reference foreground): the
    render succeeded and the PNG exists, so the pair goes into the manifest
    with the REAL (reference, render) file paths. ``pil_contract_verdict``'s
    own foreground gating then produces its own UNMEASURABLE per-predicate
    verdict for that pair -- exactly the WP4 behaviour verified in isolation
    by ``tests/test_contract_verdict.py`` and end-to-end by this tool.
*   Hard-fail with no PNG (Blender missing, subprocess non-zero, degenerate
    scene): no rendered file exists to hand to the verdict tool, so we cannot
    silently drop the view -- that would let two good views out-vote a
    broken one. Instead the view is represented in the manifest by a small
    grey sentinel pair (``sentinel.png``, ``sentinel.png``) that
    ``pil_structure_diff``'s own foreground gating flags as empty on both
    sides, forcing ``identity.silhouette_preserved`` to UNMEASURABLE and
    keeping the pair count honest. The sentinel path is echoed into
    ``per_view_renders[NAME].hard_fail`` so a reader can see exactly which
    pair was substituted and why.

Rejection paths (exit 2, byte-empty stdout, one-line stderr): unreadable or
missing contract file, ``--view`` name not in ``{front, side, back}``,
``--view`` NAME used twice, malformed ``--view NAME:PATH`` syntax, missing
scene ``.blend``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_VERSION = "0.4.0"

_HERE = Path(__file__).resolve().parent
RENDER_TOOL = _HERE / "pil_blender_render.py"
VERDICT_TOOL = _HERE / "pil_contract_verdict.py"

# The three views this tool orchestrates. Kept in a deterministic order so
# the manifest and per-view block iterate identically across runs and across
# callers who list --view arguments in different orders. --view order on the
# CLI decides ONLY which references are supplied; the manifest and payload
# always emit front, side, back so JSON diffs stay stable.
VIEW_ORDER = ("front", "side", "back")
VALID_VIEWS = frozenset(VIEW_ORDER)

# A small opaque grey square. Substituted for the render side when the
# render step hard-fails and no PNG exists to hand to pil_contract_verdict.
# 128x128 is arbitrary but small enough to keep the extra pil_structure_diff
# pass essentially free; the sentinel's purpose is to trigger
# foreground_mask_empty in the diff's own images.*.flags, which makes
# identity.silhouette_preserved return UNMEASURABLE (verified empirically:
# a solid-grey pair yields foreground mask 0px on both sides).
_SENTINEL_SIZE = 128
_SENTINEL_COLOR = (180, 180, 180)

INTERPRETATION_LIMITS = [
    "Pair convention: every --pairs manifest entry is {'a': REFERENCE, 'b': "
    "RENDER}. Directional fields in the downstream palette/structure "
    "reporting (hue_families_gained, hue_families_lost, changed_area_fraction "
    "growth direction) therefore read consistently as 'render minus "
    "reference' across the whole review. This matches pil_blender_render's "
    "own run_structure_diff argument order.",
    "A view whose render step hard-fails (Blender missing, subprocess "
    "non-zero, or a scene with no render-visible mesh geometry) is NOT "
    "silently dropped from the manifest. It is represented by a small grey "
    "sentinel pair which pil_structure_diff's own foreground gating flags as "
    "empty on both sides, forcing identity.silhouette_preserved to "
    "UNMEASURABLE for that pair. WP4's worst-case aggregation then keeps the "
    "broken view visible in the aggregate rather than letting other good "
    "views average it away. per_view_renders[NAME].hard_fail explains which "
    "pair was substituted and why. Callers whose contract does NOT include "
    "identity.silhouette_preserved should be aware that the sentinel pair "
    "may SATISFY other predicates (two identical solid-grey images have "
    "structural_similarity 1.0 and identical palettes) -- a hard-fail is "
    "only guaranteed to surface via a predicate the sentinel forces to "
    "UNMEASURABLE.",
    "A view whose render succeeded but whose comparison the render layer "
    "REFUSED (e.g. reference foreground was empty or too small) is NOT "
    "substituted with a sentinel: the render file exists, and the real "
    "(reference, render) pair goes into the manifest so pil_contract_verdict "
    "runs its own foreground gating on the real images. per_view_renders "
    "carries the render layer's own comparison.refused reason for that view "
    "so the refusal is visible without cross-referencing pair verdicts.",
    "Aggregation is delegated wholesale to pil_contract_verdict --pairs, "
    "which is worst-case per contract item (any VIOLATED pair -> VIOLATED, "
    "else any UNMEASURABLE -> UNMEASURABLE, else SATISFIED). This tool does "
    "not re-implement aggregation; it composes the manifest and echoes the "
    "verdict.",
    "Per-view render determinism follows pil_blender_render's scoped claim: "
    "same-machine, same-install byte-identical PNGs; cross-machine is not "
    "claimed. This tool's own JSON payload is fully deterministic given the "
    "same set of already-rendered input images -- the render step's scoped "
    "determinism applies underneath, but does not extend to this composition "
    "layer.",
]


class ViewSpecError(ValueError):
    """Raised for malformed --view arguments or duplicate view names."""


def parse_view_arg(raw: str) -> tuple[str, Path]:
    """Parse a single --view NAME:PATH argument.

    Windows paths carry drive-letter colons (``C:/...``), so we split on the
    FIRST colon only and require NAME to be one of the three valid view
    names. That resolves the drive-letter ambiguity without needing a
    different delimiter, which would have diverged from the plan.
    """
    if ":" not in raw:
        raise ViewSpecError(
            f"--view must be NAME:PATH (got {raw!r}); NAME is one of "
            f"{sorted(VALID_VIEWS)}"
        )
    name, _, path = raw.partition(":")
    name = name.strip()
    if name not in VALID_VIEWS:
        raise ViewSpecError(
            f"--view NAME must be one of {sorted(VALID_VIEWS)} (got {name!r})"
        )
    path = path.strip()
    if not path:
        raise ViewSpecError(f"--view {name}:PATH is empty; supply a reference path")
    return name, Path(path)


def _reject(reason: str) -> int:
    """Rejection: exit 2, byte-empty stdout, one-line stderr with tool prefix."""
    sys.stderr.write(f"pil_character_sheet_review: {reason}\n")
    return 2


def render_view(
    blend: Path,
    view: str,
    reference: Path,
    out_path: Path,
    blender_executable: str | None,
) -> tuple[dict | None, str | None]:
    """Invoke pil_blender_render.py as a subprocess for one (view, reference).

    Returns (payload_dict, error). The payload is the pil_blender_render
    JSON on success; error is a one-line reason when the subprocess itself
    hard-failed (exit non-zero, unparseable stdout). A payload whose
    ``render.rendered`` is False or whose ``comparison.refused`` is True is
    still a returned payload -- refusal is data the caller reads, not an
    error.
    """
    cmd = [
        sys.executable,
        str(RENDER_TOOL),
        str(blend),
        "--view", view,
        "--out", str(out_path),
        "--reference", str(reference),
    ]
    if blender_executable:
        cmd += ["--blender-executable", blender_executable]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        return None, f"pil_blender_render spawn failed: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:]
        detail = tail[0] if tail else "no stderr"
        return None, f"pil_blender_render exited {proc.returncode}: {detail}"
    try:
        return json.loads(proc.stdout), None
    except ValueError as exc:
        return None, f"pil_blender_render emitted non-JSON stdout: {exc}"


def write_sentinel(path: Path) -> None:
    """Write the hard-fail sentinel PNG: opaque solid grey, small.

    A solid-colour PNG has an empty foreground mask under
    pil_common.foreground_mask's border-median rule (all border pixels are
    the same colour as all interior pixels, so nothing survives the mask).
    That empty mask forces pil_structure_diff to raise
    foreground_mask_empty on both images.*.flags, which then makes
    pil_contract_verdict's identity.silhouette_preserved return
    UNMEASURABLE for the pair.
    """
    from PIL import Image, PngImagePlugin

    image = Image.new("RGB", (_SENTINEL_SIZE, _SENTINEL_SIZE), _SENTINEL_COLOR)
    empty = PngImagePlugin.PngInfo()
    image.save(path, format="PNG", pnginfo=empty, optimize=False, compress_level=6)


def run_verdict(
    contract_path: Path,
    manifest_path: Path,
    thresholds_path: Path | None = None,
    foreground: bool = True,
) -> tuple[dict | None, str | None]:
    """Invoke pil_contract_verdict.py --pairs on the built manifest.

    Returns (payload_dict, error). --foreground is passed through so palette
    and structure analysis both run in the foreground-only mode that the
    B2 render layer's alpha renders are designed for. --thresholds is
    forwarded when supplied so a caller whose workflow expects a different
    ssim/iou floor than the calibrated defaults can supply its own bundle;
    when absent, pil_contract_verdict itself defaults to
    scripts/detection_limits.json.
    """
    cmd = [
        sys.executable,
        str(VERDICT_TOOL),
        "--contract", str(contract_path),
        "--pairs", str(manifest_path),
    ]
    if thresholds_path is not None:
        cmd += ["--thresholds", str(thresholds_path)]
    if foreground:
        cmd.append("--foreground")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        return None, f"pil_contract_verdict spawn failed: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:]
        detail = tail[0] if tail else "no stderr"
        return None, f"pil_contract_verdict exited {proc.returncode}: {detail}"
    try:
        return json.loads(proc.stdout), None
    except ValueError as exc:
        return None, f"pil_contract_verdict emitted non-JSON stdout: {exc}"


def build_manifest(entries: list[dict]) -> list[dict]:
    """Extract a pil_contract_verdict --pairs manifest from per-view entries.

    Every entry contributes exactly one manifest row, including hard-fail
    entries (whose ``pair_a`` and ``pair_b`` point at the sentinel). This
    is load-bearing: silently dropping a hard-fail view would let two good
    views out-vote a broken one, exactly the WP4 property that would
    quietly break.
    """
    return [{"a": entry["pair_a"], "b": entry["pair_b"]} for entry in entries]


def build_per_view_block(entries: list[dict]) -> dict:
    """Traceability sidecar: which manifest row backed which named view.

    Sorted by view name so the JSON diff is stable regardless of --view CLI
    order. Includes the render-layer's own comparison sub-payload when a
    render succeeded, and a hard_fail block naming the substituted sentinel
    when it did not.
    """
    by_view = {}
    for entry in entries:
        name = entry["view"]
        block = {
            "reference": entry["reference"],
            "manifest_pair": {"a": entry["pair_a"], "b": entry["pair_b"]},
        }
        if entry["hard_fail"] is None:
            block["hard_fail"] = None
            block["render_payload"] = entry["render_payload"]
        else:
            block["hard_fail"] = entry["hard_fail"]
            block["render_payload"] = None
        by_view[name] = block
    return {name: by_view[name] for name in sorted(by_view)}


def build_payload(
    blend: Path,
    contract_path: Path,
    entries: list[dict],
    verdict_payload: dict,
    blender_executable: str | None,
    thresholds_path: Path | None = None,
) -> dict:
    """Deterministic tool payload; sort_keys at dump time locks byte layout."""
    parameters = {
        "blend": str(blend),
        "blender_executable": blender_executable,
        "contract": str(contract_path),
        "foreground": True,
        # Echo the resolved manifest so a reader does not need to open
        # per_view_renders to see what pil_contract_verdict was handed.
        "manifest": build_manifest(entries),
        "thresholds": str(thresholds_path) if thresholds_path is not None else None,
        "views": sorted({entry["view"] for entry in entries}),
    }
    return {
        "tool": "pil_character_sheet_review",
        "version": TOOL_VERSION,
        "parameters": parameters,
        "verdict": verdict_payload,
        "per_view_renders": build_per_view_block(entries),
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def _make_entry(
    view: str,
    reference: Path,
    render_payload: dict | None,
    rendered_path: Path | None,
    hard_fail: str | None,
    sentinel_path: Path | None,
) -> dict:
    """Assemble one per-view entry with the manifest pair already resolved.

    The three possible states, in the priority the code below hits them:

    *   hard_fail is set (Blender missing, non-zero exit, or scene refusal
        producing no PNG): manifest pair is (sentinel, sentinel), the
        rendered file does not exist, and the hard_fail reason surfaces
        into per_view_renders.
    *   render succeeded (render.rendered is True): manifest pair is
        (reference, rendered_path). This includes the case where the
        render layer's own comparison.refused is True on account of
        images.*.flags -- the file exists, and pil_contract_verdict runs
        its own foreground gating on it downstream.
    *   render was refused at the render layer (render.rendered is False,
        no PNG produced): fall through to the hard_fail path via
        sentinel substitution -- see main().
    """
    if hard_fail is not None:
        assert sentinel_path is not None
        pair_a = str(sentinel_path)
        pair_b = str(sentinel_path)
    else:
        assert rendered_path is not None
        pair_a = str(reference)
        pair_b = str(rendered_path)
    return {
        "view": view,
        "reference": str(reference),
        "render_payload": render_payload,
        "rendered_path": str(rendered_path) if rendered_path is not None else None,
        "hard_fail": hard_fail,
        "pair_a": pair_a,
        "pair_b": pair_b,
    }


def _collect_views(view_args: list[str]) -> list[tuple[str, Path]]:
    """Parse and de-duplicate --view args; raise ViewSpecError on any problem.

    Order preserved as given; the emitted per-view block re-sorts, but the
    manifest and the render-invocation order follow the caller's --view
    order so a reader can trace verdict rows back to their CLI position.
    """
    seen = {}
    ordered = []
    for raw in view_args:
        name, path = parse_view_arg(raw)
        if name in seen:
            raise ViewSpecError(
                f"--view {name!r} supplied more than once "
                f"(first: {seen[name]}, again: {path})"
            )
        seen[name] = path
        ordered.append((name, path))
    return ordered


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Compose per-view Blender renders into one contract verdict over "
            "N registered (reference, render) pairs. Delegates rendering to "
            "pil_blender_render.py and aggregation to pil_contract_verdict.py "
            "--pairs so nothing here re-implements either responsibility."
        )
    )
    parser.add_argument("blend", help="path to a .blend file")
    parser.add_argument(
        "--contract",
        required=True,
        help="JSON contract: {expect_change: [...], invariant: [...]}",
    )
    parser.add_argument(
        "--view",
        action="append",
        required=True,
        metavar="NAME:PATH",
        help=(
            "one --view NAME:PATH per rendered view; NAME is one of "
            "front/side/back; PATH points at the reference image for that "
            "view. Repeatable; each NAME may appear at most once."
        ),
    )
    parser.add_argument(
        "--blender-executable",
        default=None,
        help="path to blender.exe; forwarded to pil_blender_render.py",
    )
    parser.add_argument(
        "--thresholds",
        default=None,
        help=(
            "optional path to a pil_contract_verdict thresholds bundle; "
            "forwarded via --thresholds. When absent, pil_contract_verdict "
            "itself defaults to scripts/detection_limits.json, whose "
            "structural_similarity and silhouette_iou thresholds were "
            "calibrated on 2D image pairs and are stricter than what an "
            "auto-framed Workbench render vs an externally-authored "
            "turnaround-sheet crop typically clears; callers with a lower "
            "match-quality bar should supply their own bundle."
        ),
    )
    args = parser.parse_args(argv)

    contract_path = Path(args.contract)
    if not contract_path.is_file():
        return _reject(f"contract file not found: {contract_path}")
    try:
        contract_text = contract_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _reject(f"cannot read contract file {contract_path}: {exc}")
    try:
        json.loads(contract_text)
    except ValueError as exc:
        return _reject(f"invalid JSON in contract file {contract_path}: {exc}")

    thresholds_path = None
    if args.thresholds is not None:
        thresholds_path = Path(args.thresholds)
        if not thresholds_path.is_file():
            return _reject(f"thresholds file not found: {thresholds_path}")

    try:
        views = _collect_views(args.view)
    except ViewSpecError as exc:
        return _reject(str(exc))

    blend = Path(args.blend)
    if not blend.is_file():
        return _reject(f"blend file not found: {blend}")

    with tempfile.TemporaryDirectory(prefix="pil_char_sheet_") as td:
        workdir = Path(td)
        sentinel_path = workdir / "hard_fail_sentinel.png"
        sentinel_written = False

        entries = []
        for view, reference in views:
            out_path = workdir / f"{view}.png"
            # References that do not exist trip pil_blender_render's own
            # exit-2 rejection ("reference file not found"), which lands
            # here as a hard-fail. That is exactly the criterion-3 path:
            # unreadable reference -> UNMEASURABLE aggregate entry via
            # sentinel substitution below.
            render_payload, error = render_view(
                blend, view, reference, out_path, args.blender_executable
            )
            if error is not None:
                if not sentinel_written:
                    write_sentinel(sentinel_path)
                    sentinel_written = True
                entries.append(
                    _make_entry(
                        view=view,
                        reference=reference,
                        render_payload=None,
                        rendered_path=None,
                        hard_fail={
                            "reason": error,
                            "sentinel_pair": [
                                str(sentinel_path),
                                str(sentinel_path),
                            ],
                        },
                        sentinel_path=sentinel_path,
                    )
                )
                continue

            if not render_payload["render"]["rendered"]:
                if not sentinel_written:
                    write_sentinel(sentinel_path)
                    sentinel_written = True
                entries.append(
                    _make_entry(
                        view=view,
                        reference=reference,
                        render_payload=render_payload,
                        rendered_path=None,
                        hard_fail={
                            "reason": (
                                "render refused: "
                                + (render_payload["render"].get("refused_reason") or "unknown")
                            ),
                            "sentinel_pair": [
                                str(sentinel_path),
                                str(sentinel_path),
                            ],
                        },
                        sentinel_path=sentinel_path,
                    )
                )
                continue

            # Render succeeded. Whether comparison.refused is True or False
            # at the render layer, the real (reference, render) PNG pair
            # goes into the manifest -- pil_contract_verdict runs its own
            # foreground gating on the actual files downstream.
            entries.append(
                _make_entry(
                    view=view,
                    reference=reference,
                    render_payload=render_payload,
                    rendered_path=out_path,
                    hard_fail=None,
                    sentinel_path=None,
                )
            )

        manifest_path = workdir / "manifest.json"
        manifest_path.write_text(
            json.dumps(build_manifest(entries), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        verdict_payload, verdict_error = run_verdict(
            contract_path,
            manifest_path,
            thresholds_path=thresholds_path,
            foreground=True,
        )
        if verdict_payload is None:
            return _reject(verdict_error)

        payload = build_payload(
            blend=blend,
            contract_path=contract_path,
            entries=entries,
            verdict_payload=verdict_payload,
            blender_executable=args.blender_executable,
            thresholds_path=thresholds_path,
        )
        json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
        sys.stdout.write("\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
