from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_canon_v4_production_pack_has_exactly_four_verified_provider_assets() -> None:
    root = Path(__file__).resolve().parents[2] / "assets" / "canon" / "v4"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["format"] == "catflow-canon-pack-v1"
    assert manifest["specVersion"] == 4
    assert manifest["identityAuthority"] == {
        "childAge": "6-7",
        "childHeightCm": 120,
        "childHeightRangeCm": [115, 125],
        "childBodyProportion": "4.5-5-heads",
        "childHair": "jaw-length-short",
        "catPattern": "gray-white-tabby",
    }
    assert [asset["role"] for asset in manifest["assets"]] == [
        "episode_child",
        "episode_cat",
        "pair_scale",
        "style_board",
    ]
    assert all(asset["providerEligible"] is True for asset in manifest["assets"])

    for asset in manifest["assets"]:
        payload = (root / asset["file"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == asset["sha256"]


def test_design_lineage_and_watermarked_pair_are_never_provider_eligible() -> None:
    root = Path(__file__).resolve().parents[2] / "assets" / "canon" / "v4"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    provider_hashes = {asset["sha256"] for asset in manifest["assets"]}

    assert all(item["providerEligible"] is False for item in manifest["lineage"])
    assert "5ca63a0ee03b351ffdfdd2a7f9bbc29810c6142e913d409f5eb62f14d05f1423" not in provider_hashes
    assert all("三视图" not in item["sourcePath"] for item in manifest["lineage"])
