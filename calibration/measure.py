#!/usr/bin/env python
"""Run the bundled tools over the calibration corpus and collect metric values.

The tools are invoked as CLIs through ``subprocess``, exactly the way an agent
invokes them and the way ``tests/conftest.run_tool`` does, rather than by
importing their functions. Calibrating the importable functions would calibrate
something the caller never runs.

Two deliberate choices worth reading before changing anything:

*   **The tools are snapshotted before the run.** ``scripts/*.py`` is copied to a
    scratch directory and every measurement runs against that copy, with the
    sha256 of each file recorded. Other work happens in this tree concurrently;
    without the snapshot a single run could measure two different versions of a
    tool and silently mix them.
*   **Only the *pair* metrics are collected in-process.** Two derivations need
    more than the CLI emits -- the per-pixel luminance-delta distribution behind
    CHANGE_THRESHOLD, and the per-cell foreground support behind
    CELL_MIN_SUPPORT_PIXELS -- so those are computed by importing the
    snapshotted ``pil_common`` / ``pil_structure_diff`` and reusing their own
    primitives. Reimplementing them here would calibrate a different tool.

The corpus is four blocks:

1.  **No-change controls** -- identical re-save, PNG re-encode, rescale round
    trip, and sub-threshold perturbation. n = 400 full-frame, which is what
    buys alpha = 0.01 under the scope's ``n >~ 3/alpha`` rule.
2.  **The perturbation grid** -- magnitude x extent, every level ground-truthed.
3.  **A foreground-fraction sweep** -- for BACKGROUND_DOMINANT_MAX and
    FOREGROUND_MIN_FRACTION, which are statements about how small a subject can
    get before the numbers stop describing it.
4.  **An accent-area sweep** -- for ACCENT_AREA_SMALL_FRACTION, likewise.
5.  **An RGBA foreground control family** -- the existing ALPHA_CORPUS crossed
    with RGB-only controls and a premultiplied rescale path; JPEG is recorded as
    skipped because it cannot carry alpha.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

CALIBRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = CALIBRATION_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"

sys.path.insert(0, str(CALIBRATION_DIR))

import perturb  # noqa: E402
import scenes  # noqa: E402

PALETTE_TOOL = "pil_palette_diff.py"
STRUCTURE_TOOL = "pil_structure_diff.py"

# Candidate per-pixel luminance deltas swept for the CHANGE_THRESHOLD
# derivation. Bracket the current value (10) by a factor of three either way.
CHANGE_THRESHOLD_CANDIDATES = (1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 30)

# Diff keys treated as perceptual-colour metrics if WP1's pil_color.py has
# landed and the palette tool has started emitting them. Matched case
# insensitively as substrings, because WP1 owns the final field names.
DE2000_KEY_HINTS = ("de2000", "delta_e", "deltae", "de00")

# Modules imported from the snapshot, populated by _bind_snapshot().
_pil_common = None
_pil_structure = None

_ANALYSE_CACHE = {}
_ANALYSE_LOCK = threading.Lock()


# --- Tool snapshot and invocation --------------------------------------------


def snapshot_tools(dest):
    """Copy scripts/*.py into ``dest``; return {filename: sha256}.

    Copying rather than referencing pins the measured code for the whole run.
    The scripts resolve their own imports relative to their file, so the copy is
    self-contained.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for src in sorted(SCRIPTS.glob("*.py")):
        data = src.read_bytes()
        (dest / src.name).write_bytes(data)
        hashes[src.name] = hashlib.sha256(data).hexdigest()
    missing = [t for t in (PALETTE_TOOL, STRUCTURE_TOOL) if t not in hashes]
    if missing:
        raise SystemExit(f"missing required tool(s) in {SCRIPTS}: {', '.join(missing)}")
    return hashes


def _bind_snapshot(tools_dir):
    """Import the snapshotted primitives once, before any worker starts."""
    global _pil_common, _pil_structure
    tools_dir = str(Path(tools_dir).resolve())
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import pil_common  # noqa: PLC0415
    import pil_structure_diff  # noqa: PLC0415

    _pil_common = pil_common
    _pil_structure = pil_structure_diff
    return pil_common, pil_structure_diff


def run_tool(tools_dir, script_name, *args):
    """Invoke a bundled CLI the way an agent would, and parse its JSON.

    Mirrors tests/conftest.run_tool, including the failure message, so a broken
    tool reports identically here and in the suite.
    """
    cmd = [sys.executable, str(Path(tools_dir) / script_name), *[str(a) for a in args]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise AssertionError(
            f"{script_name} exited {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


# --- Image factory ------------------------------------------------------------


def spec_key(spec):
    return json.dumps(spec, sort_keys=True, separators=(",", ":"))


def spec_path(workdir, spec):
    digest = hashlib.sha1(spec_key(spec).encode("utf-8")).hexdigest()[:16]
    return Path(workdir) / f"{spec['scene']}_{digest}.png"


def build_image(workdir, spec):
    """Materialise one image spec to disk; return its path.

    ``spec`` is fully explicit -- scene, scene params, optional perturbation and
    its params, and PNG save options -- so the filename (a hash of the spec) is
    stable across runs and two identical specs share one file.
    """
    path = spec_path(workdir, spec)
    if spec.get("alpha_scene"):
        img = scenes.build_alpha(
            spec["alpha_scene"],
            composite=bool(spec.get("composite", False)),
            **spec.get("scene_params", {}),
        )
    else:
        img = scenes.build(spec["scene"], **spec.get("scene_params", {}))
    if spec.get("op"):
        img = perturb.apply(spec["op"], img, spec.get("op_params", {}))
    img.save(path, format="PNG", **spec.get("save", {}))
    return path


# --- Metric extraction --------------------------------------------------------


def _abs_or_none(value):
    return None if value is None else abs(float(value))


def discover_de2000_keys(palette_diff):
    """Numeric diff fields that look like perceptual-colour distances.

    WP1 (scripts/pil_color.py) is being built in parallel with this work and owns
    the final field names, so they are discovered rather than assumed. When
    nothing matches, the perceptual-colour section of the derivation is marked
    pending rather than fabricated.
    """
    found = []
    for key, value in palette_diff.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        lowered = key.lower()
        if any(hint in lowered for hint in DE2000_KEY_HINTS):
            found.append(key)
    return sorted(found)


def extract_metrics(palette, structure):
    """Collapse both tool payloads into one flat metric dict.

    Every metric is oriented as a *distance*: zero means "the tools saw no
    difference", larger means "more different". ``structural_similarity`` is
    therefore recorded as ``structural_dissimilarity = 1 - similarity`` so that
    a single upper-quantile threshold rule applies to every metric without
    special cases.
    """
    metrics = {}
    diagnostics = {}

    if palette is not None:
        diff = palette["diff"]
        image_a, image_b = palette["images"]["a"], palette["images"]["b"]
        deltas = [abs(v) for v in diff["hue_family_fraction_deltas"].values()]
        metrics["hue_family_delta_max"] = round(max(deltas), 6) if deltas else 0.0
        metrics["hue_family_delta_l1"] = round(float(sum(deltas)), 6)
        metrics["accent_fraction_delta_abs"] = _abs_or_none(diff["accent_fraction_delta"])
        metrics["luminance_mean_delta_abs"] = _abs_or_none(diff["luminance_mean_delta"])
        metrics["saturation_mean_delta_abs"] = _abs_or_none(diff["saturation_mean_delta"])
        metrics["entropy_delta_abs"] = _abs_or_none(diff["entropy_delta"])
        metrics["base_palette_distance"] = _abs_or_none(diff["base_palette_distance"])
        metrics["accent_palette_distance"] = _abs_or_none(diff["accent_palette_distance"])
        for key in discover_de2000_keys(diff):
            metrics[key] = _abs_or_none(diff[key])

        diagnostics["accent_hue_shift_detected"] = bool(diff["accent_hue_shift_detected"])
        diagnostics["hue_families_lost"] = list(diff["hue_families_lost"])
        diagnostics["hue_families_gained"] = list(diff["hue_families_gained"])
        diagnostics["palette_diff_flags"] = list(diff["flags"])
        for side, image in (("a", image_a), ("b", image_b)):
            diagnostics[f"accent_pixel_fraction_{side}"] = image["accent_pixel_fraction"]
            diagnostics[f"palette_flags_{side}"] = list(image["flags"])
            diagnostics[f"foreground_fraction_{side}"] = image["foreground"]["fraction_of_frame"]
            diagnostics[f"foreground_applied_{side}"] = bool(image["foreground"]["applied"])
            diagnostics[f"hue_families_{side}"] = {
                name: [
                    value["pixels"],
                    value["fraction_of_accents"],
                    value["fraction_of_frame"],
                ]
                for name, value in image["hue_families"].items()
            }

    if structure is not None:
        diff = structure["diff"]
        similarity = diff["structural_similarity"]
        metrics["structural_dissimilarity"] = (
            None if similarity is None else round(1.0 - float(similarity), 6)
        )
        metrics["dhash_distance"] = float(diff["dhash_distance"])
        metrics["ahash_distance"] = float(diff["ahash_distance"])
        metrics["changed_area_fraction"] = float(diff["changed_area_fraction"])
        diagnostics["cells_compared"] = diff["cells_compared"]
        diagnostics["cells_skipped_low_support"] = diff["cells_skipped_low_support"]
        diagnostics["structure_diff_flags"] = list(diff["flags"])
        diagnostics["changed_region_bbox_fractional"] = diff["changed_region_bbox_fractional"]
        for side in ("a", "b"):
            image = structure["images"][side]
            diagnostics[f"structure_flags_{side}"] = list(image["flags"])
            diagnostics[f"structure_foreground_fraction_{side}"] = image["foreground"][
                "fraction_of_frame"
            ]

    return metrics, diagnostics


# --- In-process derivations the CLI does not expose ---------------------------


def changed_area_by_threshold(path_a, path_b):
    """Changed-area fraction at each candidate CHANGE_THRESHOLD.

    Reproduces pil_structure_diff.changed_area_fraction exactly -- same working
    resize, same luminance definition -- but sweeps the threshold instead of
    hard-coding it, which is what turns CHANGE_THRESHOLD from a constant into a
    measurable choice. Full-frame only: in foreground mode the tool masks and
    crops first, and mirroring that here would duplicate its logic rather than
    reuse it.
    """
    working_a = _pil_common.to_working(_pil_common.load_rgb(path_a))
    working_b = _pil_common.to_working(_pil_common.load_rgb(path_b))
    if working_a.size != working_b.size:
        from PIL import Image  # noqa: PLC0415

        working_b = working_b.resize(working_a.size, Image.LANCZOS)
    delta = np.abs(
        _pil_common.luminance_array(working_a) - _pil_common.luminance_array(working_b)
    )
    total = float(delta.size)
    return {
        str(t): round(float((delta > t).sum()) / total, 8)
        for t in CHANGE_THRESHOLD_CANDIDATES
    }


def _cells_for(path):
    """Foreground-mode cell statistics for one image, cached by path."""
    key = str(path)
    with _ANALYSE_LOCK:
        cached = _ANALYSE_CACHE.get(key)
    if cached is not None:
        return cached
    analysis, _working = _pil_structure.analyse(path, 4, 3, True)
    cells = analysis["cells"]
    with _ANALYSE_LOCK:
        _ANALYSE_CACHE[key] = cells
    return cells


def cell_support_samples(path_a, path_b):
    """(support, statistical divergence) per corresponding cell pair.

    CELL_MIN_SUPPORT_PIXELS decides which grid cells are allowed to vote on
    structural similarity. To derive it we need the divergence a cell reports
    *as a function of how many foreground pixels it contains*, on pairs that did
    not actually change -- that curve is the noise floor per support level. The
    four features and their scales come from pil_structure_diff itself.
    """
    cells_a = _cells_for(path_a)
    index_b = {(c["row"], c["col"]): c for c in _cells_for(path_b)}
    scales = _pil_structure.FEATURE_SCALES

    samples = []
    for cell in cells_a:
        other = index_b.get((cell["row"], cell["col"]))
        if other is None:
            continue
        support_a = cell.get("foreground_pixels")
        support_b = other.get("foreground_pixels")
        if not support_a or not support_b:
            continue
        diffs = [
            min(1.0, abs(cell[feature] - other[feature]) / scale)
            for feature, scale in scales.items()
        ]
        samples.append(
            [int(min(support_a, support_b)), round(float(np.mean(diffs)), 6)]
        )
    return samples


# --- Plan construction --------------------------------------------------------

# (recipe, family, op, op_params, png save options)
CONTROL_RECIPES = (
    ("identical", "identical", None, {}, {}),
    ("png_reencode_c0", "png_reencode", None, {}, {"compress_level": 0}),
    ("png_reencode_c9", "png_reencode", None, {}, {"compress_level": 9}),
    ("png_reencode_optimize", "png_reencode", None, {}, {"optimize": True}),
    ("rescale_roundtrip_075", "rescale_roundtrip", "rescale_roundtrip", {"factor": 0.75}, {}),
    ("rescale_roundtrip_125", "rescale_roundtrip", "rescale_roundtrip", {"factor": 1.25}, {}),
    ("rescale_roundtrip_150", "rescale_roundtrip", "rescale_roundtrip", {"factor": 1.5}, {}),
    ("rescale_roundtrip_200", "rescale_roundtrip", "rescale_roundtrip", {"factor": 2.0}, {}),
    ("exposure_plus_1", "subthreshold_exposure", "exposure_shift", {"delta": 1}, {}),
    ("exposure_minus_1", "subthreshold_exposure", "exposure_shift", {"delta": -1}, {}),
    ("noise_sigma_05_a", "subthreshold_noise", "add_noise", {"sigma": 0.5, "seed_offset": 0}, {}),
    ("noise_sigma_05_b", "subthreshold_noise", "add_noise", {"sigma": 0.5, "seed_offset": 1}, {}),
    ("noise_sigma_10_a", "subthreshold_noise", "add_noise", {"sigma": 1.0, "seed_offset": 2}, {}),
    ("noise_sigma_10_b", "subthreshold_noise", "add_noise", {"sigma": 1.0, "seed_offset": 3}, {}),
    ("noise_sigma_10_c", "subthreshold_noise", "add_noise", {"sigma": 1.0, "seed_offset": 4}, {}),
    ("saturation_098", "subthreshold_saturation", "saturation_scale", {"factor": 0.98}, {}),
    ("saturation_102", "subthreshold_saturation", "saturation_scale", {"factor": 1.02}, {}),
    ("hsv_roundtrip_0deg", "hsv_roundtrip", "hue_rotate", {"degrees": 0, "extent": 1.0}, {}),
    ("hue_1deg", "subthreshold_hue", "hue_rotate", {"degrees": 1, "extent": 1.0}, {}),
    ("blur_025", "subthreshold_blur", "gaussian_blur", {"radius": 0.25}, {}),
)

# The opaque corpus predates W2 and is frozen for A2.1. RGBA gets an explicit
# JPEG slot so the inapplicable recipe is visible in the bundle rather than
# silently disappearing from the family count.
RGBA_CONTROL_RECIPES = CONTROL_RECIPES + (
    ("jpeg_reencode_q85", "jpeg_reencode", "jpeg_reencode", {"quality": 85}, {}),
)
RGBA_SKIPPED_RECIPES = {
    "jpeg_reencode_q85": "JPEG has no alpha channel; RGBA controls skip it by design."
}

# Reduced control set for the sweeps, where the question is how the noise floor
# moves with foreground / accent area rather than what it is in absolute terms.
SWEEP_CONTROL_RECIPES = (
    "identical",
    "png_reencode_c9",
    "rescale_roundtrip_150",
    "noise_sigma_10_a",
)
ACCENT_SWEEP_CONTROL_RECIPES = SWEEP_CONTROL_RECIPES + ("hsv_roundtrip_0deg", "hue_1deg")

QUICK_CONTROL_RECIPES = ("identical", "png_reencode_c9", "noise_sigma_10_a", "blur_025")


def _resolve_params(op_params, *seed_parts):
    """Replace a symbolic ``seed_offset`` with a concrete derived seed."""
    params = dict(op_params)
    if "seed_offset" in params:
        offset = params.pop("seed_offset")
        params["seed"] = perturb.derive_seed(*seed_parts, offset)
    return params


def _unit(**kwargs):
    kwargs.setdefault("magnitude", None)
    kwargs.setdefault("magnitude_unit", None)
    kwargs.setdefault("extent", 1.0)
    kwargs.setdefault("tools", ("palette", "structure"))
    kwargs.setdefault("scene_label", kwargs["scene"])
    return kwargs


def _control_units(scene, scene_params, seed, recipes, modes, kind, scene_label, tools):
    base = {"scene": scene, "scene_params": dict(scene_params)}
    units = []
    lookup = {r[0]: r for r in CONTROL_RECIPES}
    for name in recipes:
        _, family, op, op_params, save = lookup[name]
        spec_b = {
            "scene": scene,
            "scene_params": dict(scene_params),
            "op": op,
            "op_params": _resolve_params(op_params, scene, scene_label, seed, name),
            "save": dict(save),
        }
        if op is None and not save:
            # Byte-identical re-save: mark the copy so it gets its own file.
            # Two paths, identical bytes -- the true zero point of every metric.
            spec_b = dict(spec_b, duplicate=True)
        for mode in modes:
            units.append(
                _unit(
                    unit_id=f"{kind}|{family}|{name}|{scene_label}|{seed}|{mode}",
                    kind=kind,
                    family=family,
                    level=name,
                    scene=scene,
                    scene_label=scene_label,
                    scene_seed=seed,
                    mode=mode,
                    spec_a=dict(base),
                    spec_b=spec_b,
                    tools=tools,
                )
            )
    return units


def _alpha_control_units(label, scene, scene_params, recipes, tools):
    """Build RGBA foreground controls while preserving the alpha source."""
    units = []
    for name, family, op, op_params, save in recipes:
        if name in RGBA_SKIPPED_RECIPES:
            continue
        spec_a = {
            "scene": label,
            "alpha_scene": scene,
            "scene_params": dict(scene_params),
        }
        spec_b = {
            "scene": label,
            "alpha_scene": scene,
            "scene_params": dict(scene_params),
            "op": op,
            "op_params": _resolve_params(op_params, "alpha", label, name),
            "save": dict(save),
        }
        if op is None and not save:
            spec_b = dict(spec_b, duplicate=True)
        units.append(
            _unit(
                unit_id=f"alpha_control|{family}|{name}|{label}|foreground_alpha",
                kind="alpha_control",
                family=family,
                level=name,
                scene=label,
                scene_label=label,
                scene_seed=None,
                mode="foreground_alpha",
                spec_a=spec_a,
                spec_b=spec_b,
                tools=tools,
            )
        )
    return units


def build_plan(quick=False):
    """The full list of measurement units. Pure data; no I/O."""
    grid = perturb.QUICK_GRID if quick else perturb.GRID
    control_seeds = scenes.CONTROL_SEEDS[:2] if quick else scenes.CONTROL_SEEDS
    grid_seeds = scenes.GRID_SEEDS[:1] if quick else scenes.GRID_SEEDS
    control_recipes = (
        QUICK_CONTROL_RECIPES if quick else tuple(r[0] for r in CONTROL_RECIPES)
    )
    fg_sweep = scenes.FOREGROUND_SWEEP[::2] if quick else scenes.FOREGROUND_SWEEP
    accent_sweep = scenes.ACCENT_SWEEP[::3] if quick else scenes.ACCENT_SWEEP

    units = []

    # 1. No-change controls for the frozen pre-W2 four-scene corpus.
    for scene in sorted(scenes.FULL_FRAME_SCENES):
        for seed in control_seeds:
            modes = ["full_frame"]
            if scene in scenes.FOREGROUND_SCENES:
                modes.append("foreground")
            units.extend(
                _control_units(
                    scene,
                    {"seed": seed},
                    seed,
                    control_recipes,
                    modes,
                    "control",
                    scene,
                    ("palette", "structure"),
            )
        )

    # W2a's foreground control set includes exactly four meaningful opaque
    # scenes, including the two added scenes. Keep these units out of the
    # full-frame set so the phase-2 full-frame thresholds remain comparable.
    for scene in sorted(scenes.FOREGROUND_SCENES):
        if scene in scenes.FULL_FRAME_SCENES:
            continue
        for seed in control_seeds:
            units.extend(
                _control_units(
                    scene,
                    {"seed": seed},
                    seed,
                    control_recipes,
                    ("foreground",),
                    "control",
                    scene,
                    ("palette", "structure"),
                )
            )

    # W2b's alpha control family uses every declared alpha scene once per
    # recipe. JPEG is represented in RGBA_CONTROL_RECIPES but skipped above;
    # run_all records that skip explicitly in the derived payload.
    for label, scene, params in scenes.ALPHA_CORPUS:
        units.extend(
            _alpha_control_units(
                label,
                scene,
                params,
                RGBA_CONTROL_RECIPES,
                ("palette", "structure"),
            )
        )

    # 2. The perturbation grid.
    for family in sorted(grid):
        for index, level in enumerate(grid[family]):
            for scene in sorted(scenes.FULL_FRAME_SCENES):
                for seed in grid_seeds:
                    spec_a = {"scene": scene, "scene_params": {"seed": seed}}
                    spec_b = {
                        "scene": scene,
                        "scene_params": {"seed": seed},
                        "op": level["op"],
                        "op_params": _resolve_params(
                            level["params"], scene, family, seed, index
                        ),
                        "save": {},
                    }
                    modes = ["full_frame"]
                    if scene in scenes.FOREGROUND_SCENES:
                        modes.append("foreground")
                    for mode in modes:
                        units.append(
                            _unit(
                                unit_id=f"perturbation|{family}|{index:02d}|{scene}|{seed}|{mode}",
                                kind="perturbation",
                                family=family,
                                level=f"{index:02d}",
                                scene=scene,
                                scene_seed=seed,
                                mode=mode,
                                magnitude=level["magnitude"],
                                magnitude_unit=level["unit"],
                                extent=level["extent"],
                                spec_a=spec_a,
                                spec_b=spec_b,
                            )
                        )

    # 3. Foreground-fraction sweep: the same object, recoloured or moved, at a
    #    range of frame shares. Measured both ways so the full-frame attenuation
    #    can be read directly against the foreground-mode result.
    for cfg in fg_sweep:
        for seed in grid_seeds:
            base_params = {
                "seed": seed,
                "scale": cfg["scale"],
                "thickness": cfg["thickness"],
                "blade": list(scenes.SWORD_BLADE),
                "accent": list(scenes.HUE_SAMPLES["cyan"]),
            }
            variants = {
                "object_recolour": {
                    **base_params,
                    "blade": [190, 120, 60],
                    "accent": list(scenes.HUE_SAMPLES["magenta"]),
                },
                "object_offset": {**base_params, "offset": [8, 0]},
            }
            for variant, params in variants.items():
                for mode in ("full_frame", "foreground"):
                    units.append(
                        _unit(
                            unit_id=f"fg_sweep|{variant}|{cfg['label']}|thin_object|{seed}|{mode}",
                            kind="fg_sweep",
                            family=variant,
                            level=cfg["label"],
                            scene="thin_object",
                            scene_label=cfg["label"],
                            scene_seed=seed,
                            mode=mode,
                            magnitude=cfg["nominal_fraction"],
                            magnitude_unit="nominal foreground fraction",
                            spec_a={"scene": "thin_object", "scene_params": dict(base_params)},
                            spec_b={
                                "scene": "thin_object",
                                "scene_params": dict(params),
                                "op": None,
                                "op_params": {},
                                "save": {},
                            },
                        )
                    )
            units.extend(
                _control_units(
                    "thin_object",
                    base_params,
                    seed,
                    SWEEP_CONTROL_RECIPES,
                    ("full_frame", "foreground"),
                    "fg_sweep_control",
                    cfg["label"],
                    ("palette", "structure"),
                )
            )

    # 4. Accent-area sweep. Palette tool only: ACCENT_AREA_SMALL_FRACTION is a
    #    statement about hue statistics, and the structure tool has no opinion
    #    about accents.
    for accent_fraction in accent_sweep:
        label = f"accent_{accent_fraction:g}"
        for seed in grid_seeds:
            params = {"seed": seed, "accent_fraction": accent_fraction}
            units.extend(
                _control_units(
                    "dark_accent",
                    params,
                    seed,
                    ACCENT_SWEEP_CONTROL_RECIPES,
                    ("full_frame",),
                    "accent_sweep_control",
                    label,
                    ("palette",),
                )
            )
            units.append(
                _unit(
                    unit_id=f"accent_sweep|hue_rotation_30|{label}|dark_accent|{seed}|full_frame",
                    kind="accent_sweep",
                    family="hue_rotation_30",
                    level=label,
                    scene="dark_accent",
                    scene_label=label,
                    scene_seed=seed,
                    mode="full_frame",
                    magnitude=accent_fraction,
                    magnitude_unit="accent fraction of frame",
                    spec_a={"scene": "dark_accent", "scene_params": dict(params)},
                    spec_b={
                        "scene": "dark_accent",
                        "scene_params": dict(params),
                        "op": "hue_rotate",
                        "op_params": {"degrees": 30, "extent": 1.0},
                        "save": {},
                    },
                    tools=("palette",),
                )
            )

    seen = set()
    for unit in units:
        if unit["unit_id"] in seen:
            raise SystemExit(f"duplicate unit_id in plan: {unit['unit_id']}")
        seen.add(unit["unit_id"])
    units.sort(key=lambda u: u["unit_id"])
    return units


# --- Execution ----------------------------------------------------------------


def _materialise(workdir, units, jobs, progress):
    """Build every distinct image the plan references."""
    Path(workdir).mkdir(parents=True, exist_ok=True)
    specs = {}
    for unit in units:
        for spec in (unit["spec_a"], unit["spec_b"]):
            specs[spec_key(spec)] = spec
    ordered = [specs[key] for key in sorted(specs)]

    paths = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(build_image, workdir, spec): spec_key(spec) for spec in ordered}
        for future in concurrent.futures.as_completed(futures):
            paths[futures[future]] = future.result()
            done += 1
            if progress and done % 100 == 0:
                progress(f"  images {done}/{len(ordered)}")
    return paths


def _measure_unit(tools_dir, unit, path_a, path_b):
    alpha_policy = None
    if unit["kind"] == "alpha_control":
        with Image.open(path_a) as image_a, Image.open(path_b) as image_b:
            alpha_a = np.asarray(image_a.convert("RGBA").getchannel("A"), dtype=np.uint8)
            alpha_b = np.asarray(image_b.convert("RGBA").getchannel("A"), dtype=np.uint8)
        if unit["family"] == "rescale_roundtrip":
            alpha_policy = "premultiplied_resample"
        else:
            alpha_policy = "unchanged"
            if not np.array_equal(alpha_a, alpha_b):
                raise AssertionError(
                    f"RGBA control {unit['unit_id']} changed alpha in an RGB-only recipe"
                )
    palette = structure = None
    fg = ["--foreground"] if unit["mode"] == "foreground" else []
    if "palette" in unit["tools"]:
        palette = run_tool(tools_dir, PALETTE_TOOL, path_a, path_b, *fg)
    if "structure" in unit["tools"]:
        structure = run_tool(tools_dir, STRUCTURE_TOOL, path_a, path_b, *fg)

    metrics, diagnostics = extract_metrics(palette, structure)

    if unit["mode"] == "full_frame" and "structure" in unit["tools"]:
        diagnostics["changed_area_by_threshold"] = changed_area_by_threshold(path_a, path_b)
    if unit["mode"] == "foreground" and unit["kind"].endswith("control"):
        diagnostics["cell_support_samples"] = cell_support_samples(path_a, path_b)

    record = {
        key: unit[key]
        for key in (
            "unit_id",
            "kind",
            "family",
            "level",
            "scene",
            "scene_label",
            "scene_seed",
            "mode",
            "magnitude",
            "magnitude_unit",
            "extent",
        )
    }
    record["metrics"] = metrics
    record["diagnostics"] = diagnostics
    if alpha_policy is not None:
        diagnostics["alpha_policy"] = alpha_policy
        diagnostics["alpha_unchanged"] = bool(alpha_policy == "unchanged")
    return record


def collect(tools_dir, workdir, quick=False, jobs=None, progress=None):
    """Materialise the corpus, run both tools over it, return the records."""
    _bind_snapshot(tools_dir)
    _ANALYSE_CACHE.clear()

    jobs = jobs or min(16, (os.cpu_count() or 4))
    units = build_plan(quick=quick)
    if progress:
        progress(f"  plan: {len(units)} measurement units, {jobs} workers")

    started = time.monotonic()
    paths = _materialise(workdir, units, jobs, progress)
    if progress:
        progress(f"  images: {len(paths)} built in {time.monotonic() - started:.1f}s")

    records = {}
    done = 0
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(
                _measure_unit,
                tools_dir,
                unit,
                paths[spec_key(unit["spec_a"])],
                paths[spec_key(unit["spec_b"])],
            ): unit["unit_id"]
            for unit in units
        }
        for future in concurrent.futures.as_completed(futures):
            records[futures[future]] = future.result()
            done += 1
            if progress and done % 200 == 0:
                rate = done / max(1e-9, time.monotonic() - started)
                remaining = (len(units) - done) / max(1e-9, rate)
                progress(f"  measured {done}/{len(units)} ({rate:.1f}/s, ~{remaining:.0f}s left)")

    if progress:
        progress(f"  measurement: {len(records)} units in {time.monotonic() - started:.1f}s")
    return [records[key] for key in sorted(records)]


def collect_units(tools_dir, workdir, units, jobs=None):
    """Measure an explicitly supplied unit list for acceptance preflights."""
    _bind_snapshot(tools_dir)
    _ANALYSE_CACHE.clear()
    jobs = jobs or min(16, (os.cpu_count() or 4))
    paths = _materialise(workdir, units, jobs, progress=None)
    records = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(
                _measure_unit,
                tools_dir,
                unit,
                paths[spec_key(unit["spec_a"])],
                paths[spec_key(unit["spec_b"])],
            ): unit["unit_id"]
            for unit in units
        }
        for future in concurrent.futures.as_completed(futures):
            records[futures[future]] = future.result()
    return [records[key] for key in sorted(records)]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect calibration measurements.")
    parser.add_argument("--out", required=True, help="path for the measurements JSON")
    parser.add_argument("--quick", action="store_true", help="reduced grid, for smoke tests")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers")
    args = parser.parse_args(argv)

    import tempfile  # noqa: PLC0415

    workdir = Path(tempfile.mkdtemp(prefix="pil-calib-"))
    try:
        hashes = snapshot_tools(workdir / "tools")
        records = collect(
            workdir / "tools",
            workdir / "images",
            quick=args.quick,
            jobs=args.jobs,
            progress=lambda msg: print(msg, file=sys.stderr),
        )
        payload = {"tool_sha256": hashes, "records": records}
        Path(args.out).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
