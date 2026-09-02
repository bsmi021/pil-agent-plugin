"""Re-run the embedding discrimination gate against a given model+profile.

Same corpus, same three pair families, same pair list as
runs/2026-08-31-embedding-discrimination, so the two runs are directly
comparable. Cosines come from pil_embed's own embed path (not a private
reimplementation) and dhash from pil_image_analyze, exactly as before.
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path("/home/user/pil-agent-plugin")
sys.path.insert(0, str(REPO / "scripts"))

import pil_image_analyze as pia  # noqa: E402
import pil_embed  # noqa: E402
from pil_common import dhash, load_rgba_straight, to_working  # noqa: E402

# The corpus is one photographer's private phone library and is NOT
# committed; these defaults are the session-local paths the recorded run
# actually read, kept so the numbers in gate-results.json are traceable to
# named files. Override both roots to re-run against another corpus with
# the same filenames.
DEFAULT_CORPUS_ROOT = Path(
    "/root/.claude/uploads/fbeed470-d1d4-5e41-9870-fab1c1fcf82e"
)
DEFAULT_PERTURBED_ROOT = Path(
    "/tmp/claude-0/-home-user-pil-agent-plugin/"
    "fbeed470-d1d4-5e41-9870-fab1c1fcf82e/scratchpad/gate_tmp"
)

PHOTO_FILES = {
    "cosmo_shoe": "31146f1f-image.jpg",
    "mm_statue": "07ebcf8d-image.jpg",
    "mm_car": "f205d839-image.jpg",
    "nyny_bridge": "b261dc20-image.jpg",
    "poodle": "ebcb1b08-image.jpg",
    "pirate_flag": "cbd27ece-image.jpg",
    "cannon": "07609dbc-image.jpg",
    "torture": "d44a575a-image.jpg",
    "pirate_skel": "e636eb4c-image.jpg",
    "lions_sign": "8ebae4c6-image.jpg",
    "lab_app": "7d208f68-image.jpg",
    "passkey": "a4564d00-image.jpg",
    "drone": "5e7e0241-image.jpg",
}
PERTURBED_FILES = {
    "rescale_50": "p_rescale.png",
    "jpeg_q60": "p_jpeg.jpg",
    "center_crop_75": "p_crop.png",
    "rotate_5deg": "p_rot.png",
}

FAMILIES = {
    "perturbation": [("cosmo_shoe", f"cosmo_shoe~{k}") for k in PERTURBED_FILES],
    "same_venue": [
        ("cannon", "lions_sign"),
        ("cosmo_shoe", "nyny_bridge"),
        ("mm_statue", "mm_car"),
        ("pirate_flag", "pirate_skel"),
        ("pirate_flag", "torture"),
    ],
    "unrelated": [
        ("drone", "cosmo_shoe"),
        ("drone", "lab_app"),
        ("lab_app", "cannon"),
        ("passkey", "drone"),
        ("passkey", "pirate_flag"),
        ("poodle", "cannon"),
        ("poodle", "mm_statue"),
        ("poodle", "nyny_bridge"),
    ],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--preprocessing", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    ap.add_argument(
        "--perturbed-root", type=Path, default=DEFAULT_PERTURBED_ROOT
    )
    args = ap.parse_args()

    paths = {k: args.corpus_root / v for k, v in PHOTO_FILES.items()}
    for key, name in PERTURBED_FILES.items():
        paths[f"cosmo_shoe~{key}"] = args.perturbed_root / name

    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise SystemExit(
            "corpus not available on this machine; missing:\n  "
            + "\n  ".join(missing)
        )

    vectors, dhashes = {}, {}
    for name, path in paths.items():
        payload = pil_embed.run_embed(
            SimpleNamespace(
                model=args.model,
                preprocessing=args.preprocessing,
                region=None,
                image_a=str(path),
                image_b=None,
            )
        )
        vectors[name] = payload["images"]["a"]["fingerprint"]["unit_values"]
        frame_rgb, _straight, _alpha = load_rgba_straight(str(path))
        dhashes[name] = pia._bits_to_hex(dhash(to_working(frame_rgb)))
        print(f"  embedded {name}", file=sys.stderr)

    results = {}
    for family, pairs in FAMILIES.items():
        rows = {}
        for left, right in pairs:
            rows[f"{left}~{right}".replace("cosmo_shoe~cosmo_shoe~", "cosmo_shoe~")] = {
                "cosine": pil_embed._cosine(vectors[left], vectors[right]),
                "dhash": pia.hex_hamming(dhashes[left], dhashes[right]),
            }
        results[family] = dict(sorted(rows.items()))

    Path(args.out).write_text(
        json.dumps(results, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    for family, rows in results.items():
        cosines = [r["cosine"] for r in rows.values()]
        print(f"{family:14s} n={len(rows)} min={min(cosines):.4f} max={max(cosines):.4f}")


if __name__ == "__main__":
    main()
