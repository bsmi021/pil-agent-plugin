import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pil_multiview_prepare import prepare_manifest


def _rgba_shape(path: Path) -> None:
    image = Image.new("RGBA", (80, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(20, 85), (40, 10), (65, 85)], fill=(40, 80, 120, 255))
    image.save(path)


def test_prepare_extracts_ordered_normalized_contours_for_seven_views(tmp_path):
    image = tmp_path / "shape.png"
    _rgba_shape(image)
    names = ["front", "front_right", "right", "back_right", "back", "back_left", "front_left"]
    manifest = {
        "schema": "multiview-spec-v1",
        "views": [{"name": name, "image": str(image)} for name in names],
    }

    payload = prepare_manifest(manifest, manifest_path=tmp_path / "spec.json")

    assert payload["status"] == "PREPARED"
    assert [view["name"] for view in payload["views"]] == names
    for view in payload["views"]:
        assert view["foreground"]["source"] == "alpha"
        assert view["foreground"]["pixel_count"] > 0
        contour = np.asarray(view["contour_normalized"])
        assert contour.shape[0] >= 3
        assert contour.shape[1] == 2
        assert np.all((contour >= 0.0) & (contour <= 1.0))
        assert view["bbox_normalized"][0] < view["bbox_normalized"][2]


def test_prepare_is_deterministic(tmp_path):
    image = tmp_path / "shape.png"
    _rgba_shape(image)
    manifest = {"schema": "multiview-spec-v1", "views": [{"name": "front", "image": str(image)}]}

    first = prepare_manifest(manifest, manifest_path=tmp_path / "spec.json")
    second = prepare_manifest(json.loads(json.dumps(manifest)), manifest_path=tmp_path / "spec.json")

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
