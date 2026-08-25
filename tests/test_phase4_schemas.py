import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
EXPECTED = {
    "multiview-spec-v1.schema.json": "multiview-spec-v1",
    "template-mesh-v1.schema.json": "template-mesh-v1",
    "correspondences-v1.schema.json": "correspondences-v1",
    "geometry-constraints-v1.schema.json": "geometry-constraints-v1",
    "render-views-v1.schema.json": "render-views-v1",
    "review-views-v1.schema.json": "review-views-v1",
    "reconstruction-job-v1.schema.json": "reconstruction-job-v1",
}


def test_phase4_schemas_are_valid_json_with_versioned_schema_discriminators():
    for filename, discriminator in EXPECTED.items():
        payload = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert payload["properties"]["schema"]["const"] == discriminator
        assert "schema" in payload["required"]
