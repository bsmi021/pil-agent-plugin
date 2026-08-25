import pytest
from pil_multiview_review import ReviewManifestError, build_pairs


def test_review_manifest_keeps_all_seven_views_in_order(tmp_path):
    views = []
    for index in range(7):
        reference = tmp_path / f"ref-{index}.png"
        render = tmp_path / f"render-{index}.png"
        reference.write_bytes(b"x")
        render.write_bytes(b"x")
        views.append({"name": f"view-{index}", "reference": str(reference), "render": str(render)})

    pairs = build_pairs({"schema": "review-views-v1", "views": views}, tmp_path / "review.json")

    assert [row["name"] for row in pairs] == [f"view-{i}" for i in range(7)]
    assert len(pairs) == 7


def test_review_manifest_refuses_missing_view_instead_of_dropping_it(tmp_path):
    manifest = {
        "schema": "review-views-v1",
        "views": [{"name": "front", "reference": "missing.png", "render": "also-missing.png"}],
    }

    with pytest.raises(ReviewManifestError, match="not found"):
        build_pairs(manifest, tmp_path / "review.json")
