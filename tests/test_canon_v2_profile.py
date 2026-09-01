from __future__ import annotations

import json
from pathlib import Path


def test_canon_v2_profile_reuses_identity_assets_without_the_photoreal_style_reference() -> None:
    profile_path = Path("风格定稿/Canon-v2/manifest.json")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    assert profile["profileId"] == "canon-v2-healing-child-cat"
    assert profile["assetSourceManifest"] == "../Canon-v1/manifest.json"
    assert profile["identityKeys"] == [
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
    ]
    assert profile["environmentStyleKeys"] == {
        "indoor": "style:indoor",
        "outdoor": "style:outdoor",
    }
    assert "style:line_texture" not in json.dumps(profile, ensure_ascii=False)
