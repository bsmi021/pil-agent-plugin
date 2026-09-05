#!/usr/bin/env python
"""Seal, verify, and compare vision claims about an image -- without ever
pretending they are measurements.

The measurement tools in this repository refuse semantic questions by design:
`identity.*` and `style.*` are UNMEASURABLE because no pixel statistic can
answer them. But the calling agent's native vision CAN answer them -- it just
answers in prose that evaporates after the conversation, unattached to any
file, unverifiable later. This tool closes that gap by making a vision claim
a first-class evidence artifact:

*   ``seal``    validates a claims file against the semantic-record-v1 shape
    and binds it to one image's exact bytes (sha256) and frame (fractional
    regions resolved to pixel rects). The output is deterministic; its
    ``record_id`` is the sha256 of the canonical binding, so the same claims
    about the same bytes always produce the same record.
*   ``verify``  checks that a sealed record describes a given file: the
    sha256 matches and every claim region fits the frame. Binding, not truth.
*   ``compare`` reports exact-match agreement between two sealed records'
    claims, per kind, with no similarity score -- lists, not verdicts.

The provenance line this tool holds, stated once and enforced everywhere:
a sealed claim is an ASSERTION by a model or person. Sealing makes it
attributable and checkable against the exact image it was made about.
It does not make it true, and nothing in this tool's output may be read
as a pixel measurement.

Usage:
    python pil_semantic_record.py seal "img.png" --claims claims.json [--claimant "..."] [--claimed-at "..."]
    python pil_semantic_record.py verify "img.png" --record record.json
    python pil_semantic_record.py compare --record-a a.json --record-b b.json

A claims file is the un-sealed input, authored by whoever looked:

    {"claims": [
      {"kind": "text_transcription", "value": "THE CHANDELIER",
       "region_fractional": [0.24, 0.17, 0.48, 0.23],
       "confidence": "high", "evidence": "pil_crop at native resolution"},
      {"kind": "landmark", "value": "The Cosmopolitan of Las Vegas",
       "confidence": "high"}
    ]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

from pil_region import RegionError, resolve_pixel_rect  # noqa: E402

TOOL_VERSION = "0.8.0"

SCHEMA_NAME = "semantic-record-v1"

CLAIM_KINDS = (
    "scene",
    "object",
    "text_transcription",
    "landmark",
    "attribute",
    "relation",
    "other",
)

CONFIDENCE_LEVELS = ("high", "medium", "low")

MAX_VALUE_LENGTH = 2000
MAX_CLAIMS = 200

INTERPRETATION_LIMITS = [
    "Every claim in a sealed record is a VISION CLAIM: an assertion by a "
    "model or person, bound to exact file bytes. Nothing in this payload is "
    "a pixel measurement, and sealing does not make a claim true -- it makes "
    "it attributable and checkable against the exact image it was made "
    "about. The measurement tools' own refusal to answer semantic questions "
    "(identity.*, style.* are UNMEASURABLE) is unchanged; this tool is where "
    "those answers live when vision supplies them.",
    "verify checks BINDING, not truth: verified true means the record was "
    "sealed against these exact bytes and every claim region fits inside "
    "this frame -- never that any claim is correct. A record that fails "
    "verification says nothing about the claims either; it says you are "
    "holding the wrong file.",
    "compare reports exact-match agreement after Unicode NFC, case, and "
    "whitespace normalisation, per claim kind. It emits NO similarity "
    "score: two wordings of the same true fact count as a disagreement, "
    "and agreement between two wrong claims is still agreement. Read the "
    "lists, not the counts.",
    "record_id is the sha256 of the canonical claims-plus-image binding: it "
    "identifies this record's exact content, changes when any claim "
    "changes, and is deterministic -- re-sealing identical claims about "
    "identical bytes reproduces it. claimed_at and claimant are "
    "caller-supplied attribution, echoed verbatim and excluded from "
    "record_id, so attribution edits do not masquerade as new claims.",
    "region_fractional is resolved to resolved_pixel_rect with the same "
    "parser and half-up rounding rule as pil_crop.py, so a claim's region "
    "can be re-cropped for inspection byte-for-byte. A claim without a "
    "region asserts something about the image as a whole.",
]


class ClaimsError(Exception):
    """A claims or record file failed validation; message says exactly why."""


def _fail(message):
    raise ClaimsError(message)


def _normalise(text):
    """NFC + casefold + whitespace-collapse, the comparison equivalence."""
    collapsed = " ".join(unicodedata.normalize("NFC", text).split())
    return collapsed.casefold()


def _validate_claim(entry, index):
    if not isinstance(entry, dict):
        _fail(f"claims[{index}] must be an object")
    unknown = set(entry) - {"kind", "value", "confidence", "evidence", "region_fractional"}
    if unknown:
        _fail(f"claims[{index}] has unknown keys: {sorted(unknown)}")
    kind = entry.get("kind")
    if kind not in CLAIM_KINDS:
        _fail(f"claims[{index}].kind must be one of {list(CLAIM_KINDS)}, got {kind!r}")
    value = entry.get("value")
    if not isinstance(value, str) or not value.strip():
        _fail(f"claims[{index}].value must be a non-empty string")
    if len(value) > MAX_VALUE_LENGTH:
        _fail(f"claims[{index}].value exceeds {MAX_VALUE_LENGTH} characters")
    confidence = entry.get("confidence")
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        _fail(
            f"claims[{index}].confidence must be one of {list(CONFIDENCE_LEVELS)} "
            f"or omitted, got {confidence!r}"
        )
    evidence = entry.get("evidence")
    if evidence is not None and (not isinstance(evidence, str) or len(evidence) > MAX_VALUE_LENGTH):
        _fail(f"claims[{index}].evidence must be a string of at most {MAX_VALUE_LENGTH} characters")
    region = entry.get("region_fractional")
    if region is not None:
        if (
            not isinstance(region, list)
            or len(region) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in region)
        ):
            _fail(f"claims[{index}].region_fractional must be [L, T, R, B] numbers")
        left, top, right, bottom = (float(v) for v in region)
        if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
            _fail(
                f"claims[{index}].region_fractional must satisfy "
                "0 <= L < R <= 1 and 0 <= T < B <= 1"
            )
        region = [left, top, right, bottom]
    return {
        "kind": kind,
        "value": value,
        "confidence": confidence,
        "evidence": evidence,
        "region_fractional": region,
    }


def _load_claims(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        _fail(f"cannot read claims file: {exc}")
    except json.JSONDecodeError as exc:
        _fail(f"claims file is not valid JSON: {exc}")
    if not isinstance(raw, dict) or "claims" not in raw:
        _fail('claims file must be an object with a "claims" array')
    unknown = set(raw) - {"claims"}
    if unknown:
        _fail(f"claims file has unknown top-level keys: {sorted(unknown)}")
    claims = raw["claims"]
    if not isinstance(claims, list) or not claims:
        _fail('"claims" must be a non-empty array')
    if len(claims) > MAX_CLAIMS:
        _fail(f"claims file exceeds {MAX_CLAIMS} claims")
    return [_validate_claim(entry, i) for i, entry in enumerate(claims)]


def _image_binding(path):
    file_path = Path(path)
    sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    with Image.open(file_path) as img:
        size = [img.width, img.height]
    return sha256, size


def _canonical_claims(claims):
    """Sort claims deterministically: kind, value, then region."""
    return sorted(
        claims,
        key=lambda c: (
            c["kind"],
            c["value"],
            c["region_fractional"] if c["region_fractional"] is not None else [],
        ),
    )


def _record_id(image_sha256, size, claims):
    """sha256 of the canonical binding. claimant/claimed_at are deliberately
    excluded so attribution edits never masquerade as new claims."""
    canonical = json.dumps(
        {"image_sha256": image_sha256, "size": size, "claims": claims},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal(image_path, claims_path, claimant, claimed_at):
    claims = _canonical_claims(_load_claims(claims_path))
    sha256, size = _image_binding(image_path)
    # record_id covers the claims as authored (no derived fields), so verify
    # can recompute it from a sealed record by stripping resolved_pixel_rect.
    record_id = _record_id(sha256, size, claims)
    for claim in claims:
        if claim["region_fractional"] is not None:
            rect = resolve_pixel_rect(claim["region_fractional"], tuple(size))
            claim["resolved_pixel_rect"] = list(rect)
        else:
            claim["resolved_pixel_rect"] = None
    record = {
        "schema": SCHEMA_NAME,
        "image": {"path": str(image_path), "sha256": sha256, "size": size},
        "source": "vision_claim",
        "claimant": claimant,
        "claimed_at": claimed_at,
        "claims": claims,
        "record_id": record_id,
    }
    return record


def _load_record(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        _fail(f"cannot read record file: {exc}")
    except json.JSONDecodeError as exc:
        _fail(f"record file is not valid JSON: {exc}")
    record = raw.get("record") if isinstance(raw, dict) and "record" in raw else raw
    if not isinstance(record, dict) or record.get("schema") != SCHEMA_NAME:
        _fail(f'record file does not carry schema "{SCHEMA_NAME}"')
    for key in ("image", "claims", "record_id", "source"):
        if key not in record:
            _fail(f"record file is missing {key!r}")
    if record["source"] != "vision_claim":
        _fail('record source must be "vision_claim"')
    claims = [
        _validate_claim(
            {k: v for k, v in entry.items() if k != "resolved_pixel_rect"}, i
        )
        for i, entry in enumerate(record["claims"])
    ]
    image = record["image"]
    if not isinstance(image, dict) or "sha256" not in image or "size" not in image:
        _fail("record image binding must carry sha256 and size")
    expected = _record_id(image["sha256"], list(image["size"]), _canonical_claims(claims))
    if record["record_id"] != expected:
        _fail(
            "record_id does not match the record's own content -- the record "
            "was edited after sealing, or sealed by an incompatible tool"
        )
    return record


def verify(image_path, record_path):
    record = _load_record(record_path)
    sha256, size = _image_binding(image_path)
    failures = []
    if record["image"]["sha256"] != sha256:
        failures.append(
            "sha256_mismatch: the record was sealed against different bytes "
            f"(record {record['image']['sha256'][:12]}..., file {sha256[:12]}...)"
        )
    if list(record["image"]["size"]) != size:
        failures.append(
            f"size_mismatch: record binds {record['image']['size']}, file is {size}"
        )
    for i, claim in enumerate(record["claims"]):
        rect = claim.get("resolved_pixel_rect")
        if rect is not None:
            left, top, right, bottom = rect
            if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
                failures.append(f"claim_region_out_of_bounds: claims[{i}] rect {rect}")
    return {
        "verified": not failures,
        "failures": failures,
        "record_id": record["record_id"],
        "image_sha256": sha256,
    }


def compare(record_a_path, record_b_path):
    record_a = _load_record(record_a_path)
    record_b = _load_record(record_b_path)

    by_kind = {}
    for kind in CLAIM_KINDS:
        values_a = sorted(
            {c["value"] for c in record_a["claims"] if c["kind"] == kind}
        )
        values_b = sorted(
            {c["value"] for c in record_b["claims"] if c["kind"] == kind}
        )
        if not values_a and not values_b:
            continue
        norm_a = {_normalise(v): v for v in values_a}
        norm_b = {_normalise(v): v for v in values_b}
        matched = sorted(norm_a[key] for key in norm_a.keys() & norm_b.keys())
        by_kind[kind] = {
            "matched": matched,
            "only_a": sorted(norm_a[key] for key in norm_a.keys() - norm_b.keys()),
            "only_b": sorted(norm_b[key] for key in norm_b.keys() - norm_a.keys()),
        }

    return {
        "same_image_bytes": record_a["image"]["sha256"] == record_b["image"]["sha256"],
        "record_a_id": record_a["record_id"],
        "record_b_id": record_b["record_id"],
        "claims_by_kind": by_kind,
        "matched_total": sum(len(v["matched"]) for v in by_kind.values()),
        "unmatched_total": sum(
            len(v["only_a"]) + len(v["only_b"]) for v in by_kind.values()
        ),
    }


def _emit(payload):
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Seal, verify, and compare vision claims about images. "
        "Claims are assertions, never measurements; sealing makes them "
        "attributable and checkable, not true."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seal_parser = sub.add_parser("seal", help="bind a claims file to an image's exact bytes")
    seal_parser.add_argument("image", help="image the claims are about")
    seal_parser.add_argument("--claims", required=True, help="claims JSON file")
    seal_parser.add_argument(
        "--claimant",
        default=None,
        help="who or what made these claims (attribution only; excluded from record_id)",
    )
    seal_parser.add_argument(
        "--claimed-at",
        default=None,
        help="when the claims were made, caller-supplied verbatim (attribution "
        "only; excluded from record_id so sealing stays deterministic)",
    )

    verify_parser = sub.add_parser(
        "verify", help="check that a sealed record describes a given file (binding, not truth)"
    )
    verify_parser.add_argument("image", help="image file to check the record against")
    verify_parser.add_argument("--record", required=True, help="sealed record JSON file")

    compare_parser = sub.add_parser(
        "compare", help="exact-match agreement between two sealed records, per claim kind"
    )
    compare_parser.add_argument("--record-a", required=True)
    compare_parser.add_argument("--record-b", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "seal":
            result = seal(args.image, args.claims, args.claimant, args.claimed_at)
            payload_key, payload_value = "record", result
            exit_code = 0
        elif args.command == "verify":
            result = verify(args.image, args.record)
            payload_key, payload_value = "verification", result
            exit_code = 0 if result["verified"] else 1
        else:
            result = compare(args.record_a, args.record_b)
            payload_key, payload_value = "comparison", result
            exit_code = 0
    except (ClaimsError, RegionError, OSError) as exc:
        print(f"pil_semantic_record: {exc}", file=sys.stderr)
        return 2

    _emit(
        {
            "tool": "pil_semantic_record",
            "version": TOOL_VERSION,
            "command": args.command,
            payload_key: payload_value,
            "interpretation_limits": INTERPRETATION_LIMITS,
        }
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
