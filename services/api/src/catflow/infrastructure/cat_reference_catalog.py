"""Register approved local reference packs without changing the global default."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from catflow.application.service import CanonRevisionCreateCommand


def initialize_cat_reference_catalog(repository, media_store, project_root: Path) -> None:
    manifest = json.loads(
        (project_root / "assets/cat-reference-options.json").read_text(encoding="utf-8")
    )
    profiles = repository.list_canon_profiles()
    existing = {item.key: item for item in repository.list_cat_reference_options()}
    for order, option in enumerate(manifest["options"]):
        # A published global default never repoints an already approved option.
        saved = existing.get(option["key"])
        if saved and saved.canon_profile_id:
            reason = None
            try:
                for asset in [*saved.fixed_assets.values(), *(a.asset for a in saved.auxiliary)]:
                    stored = repository.get_asset(asset.id)
                    path = media_store.resolve(stored.storage_key)
                    if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
                        raise ValueError("已注册参考文件的哈希不一致")
            except (OSError, ValueError) as exc:
                reason = str(exc)
            repository.save_cat_reference_option(
                key=saved.key,
                label=saved.label,
                canon_profile_id=saved.canon_profile_id,
                auxiliary=[{"view": a.view, "assetId": str(a.asset.id)} for a in saved.auxiliary],
                sort_order=order,
                unavailable_reason=reason,
            )
            continue
        canon = None
        auxiliary = []
        reason = None
        try:
            payloads = {}
            for item in [*option["fixedAssets"].values(), *option["auxiliary"]]:
                path = (project_root / item["path"]).resolve()
                if not path.is_relative_to(project_root.resolve()):
                    raise ValueError("参考路径超出项目目录")
                payload = path.read_bytes()
                if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                    raise ValueError(f"已确认参考的内容不一致：{item['path']}")
                payloads[item["path"]] = payload
            hashes = {role: item["sha256"] for role, item in option["fixedAssets"].items()}
            canon = next(
                (
                    profile
                    for profile in profiles
                    if {role: asset.sha256 for role, asset in profile.fixed_assets.items()}
                    == hashes
                    and profile.profile.get("cat") == option["cat"]
                ),
                None,
            )
            if canon is None:
                assets = {}
                for role, item in option["fixedAssets"].items():
                    media = media_store.save_upload(
                        payloads[item["path"]],
                        filename=Path(item["path"]).name,
                        declared_content_type="image/png",
                        role=role,
                    )
                    assets[role] = repository.register_canon_asset(
                        role=role,
                        sha256=media.sha256,
                        storage_key=media.storage_key,
                        byte_size=media.byte_size,
                    ).id
                canon = repository.publish_canon_revision(
                    CanonRevisionCreateCommand(
                        fixedAssets=assets, cat=option["cat"], activate=False
                    )
                )
                profiles.append(canon)
            for item in option["auxiliary"]:
                media = media_store.save_upload(
                    payloads[item["path"]],
                    filename=Path(item["path"]).name,
                    declared_content_type="image/png",
                    role="cat_reference_" + item["view"],
                )
                asset = repository.register_canon_asset(
                    role="cat_reference_" + item["view"],
                    sha256=media.sha256,
                    storage_key=media.storage_key,
                    byte_size=media.byte_size,
                )
                auxiliary.append({"view": item["view"], "assetId": str(asset.id)})
        except (OSError, ValueError) as exc:
            reason = str(exc)
        repository.save_cat_reference_option(
            key=option["key"],
            label=option["label"],
            canon_profile_id=canon.id if canon else None,
            auxiliary=auxiliary,
            sort_order=order,
            unavailable_reason=reason,
        )
