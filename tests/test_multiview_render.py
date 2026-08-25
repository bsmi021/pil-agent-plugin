import json
import re

import pytest
from pil_multiview_render import (
    ViewManifestError,
    build_probe_source,
    validate_view_manifest,
)


def _seven_views():
    return {
        "schema": "render-views-v1",
        "views": [
            {"name": "front", "direction": [0, -1, 0], "up": [0, 0, 1]},
            {"name": "front_right", "direction": [1, -1, 0], "up": [0, 0, 1]},
            {"name": "right", "direction": [1, 0, 0], "up": [0, 0, 1]},
            {"name": "back_right", "direction": [1, 1, 0], "up": [0, 0, 1]},
            {"name": "back", "direction": [0, 1, 0], "up": [0, 0, 1]},
            {"name": "back_left", "direction": [-1, 1, 0], "up": [0, 0, 1]},
            {"name": "front_left", "direction": [-1, -1, 0], "up": [0, 0, 1]},
        ],
    }


def test_seven_view_manifest_is_accepted_and_embedded_deterministically(tmp_path):
    views = validate_view_manifest(_seven_views())
    source = build_probe_source(views, tmp_path, 512, 512, 0.1, "analysis", True)

    assert len(views) == 7
    embedded = re.search(r"json\.loads\(r'''(.*?)'''\)", source, re.DOTALL)
    assert embedded is not None
    assert len(json.loads(embedded.group(1))["views"]) == 7
    assert "BVHTree" not in source


def test_parallel_direction_and_up_is_rejected():
    manifest = _seven_views()
    manifest["views"][0]["up"] = [0, -1, 0]

    with pytest.raises(ViewManifestError, match="parallel"):
        validate_view_manifest(manifest)
