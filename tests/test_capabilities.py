import json
import sys
import subprocess
from pathlib import Path
from PIL import Image
from pil_capabilities import catalog, invoke, batch


def test_catalog_contains_original_and_new_tools_with_arguments():
    tools = {t["name"]: t for t in catalog()["tools"]}
    for name in [
        "pil_palette_diff",
        "pil_blender_fit",
        "pil_normalize",
        "pil_register",
        "pil_mask",
        "pil_calibrate",
        "pil_diff_regions",
        "pil_pipeline",
    ]:
        assert name in tools
        assert tools[name]["cli_schema"]["arguments"]
        assert tools[name]["inputSchema"]["properties"]["argv"]["type"] == "array"
    assert tools["pil_blender_fit"]["mutates"]
    assert not tools["pil_palette_diff"]["mutates"]


def test_adapter_parity_and_batch_retains_failure(tmp_path):
    image = tmp_path / "a.png"
    Image.new("RGB", (30, 30), "red").save(image)
    result = invoke("pil_image_info", [str(image)])
    direct = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts/pil_image_info.py"),
            str(image),
        ],
        capture_output=True,
        text=True,
    )
    assert result["result"] == json.loads(direct.stdout)
    results = batch(
        [
            {"id": "a", "tool": "pil_image_info", "argv": [str(image)]},
            {"id": "b", "tool": "not_a_tool", "argv": []},
        ]
    )
    assert len(results["items"]) == 2
    assert results["items"][0]["ok"] and not results["items"][1]["ok"]


def test_invocation_never_accepts_shell_or_unknown_program():
    result = invoke("../evil", [])
    assert not result["ok"]
    assert "unknown" in result["error"]
