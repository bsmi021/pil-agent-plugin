import json

from pil_blender_fit import _extract_payload, build_probe_source


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
    wrapped = "noise\n<<<PIL_AGENT_BLENDER_FIT_BEGIN>>>\n" + json.dumps(body) + "\n<<<PIL_AGENT_BLENDER_FIT_END>>>\n"

    assert _extract_payload(wrapped) == body
    assert _extract_payload(wrapped + wrapped) is None

