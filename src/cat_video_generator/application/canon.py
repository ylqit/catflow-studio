"""Validated repair of the durable Canon asset set."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from ..domain.contract_base import StrictModel
from .ports import AssetStore, LandedAsset, StoredAsset


class CanonManifestEntry(StrictModel):
    semantic_key: str = Field(alias="semanticKey", min_length=3, max_length=160)
    file: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommended_default: bool = Field(alias="recommendedDefault")
    group: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    role: str | None = None


class CanonRepository(Protocol):
    def list_assets(self) -> tuple[StoredAsset, ...]: ...

    def install_canon_asset(
        self,
        *,
        landed: LandedAsset,
        semantic_key: str,
        role: str,
        display_name: str,
        group: str | None,
        recommended_default: bool,
    ) -> StoredAsset: ...

    def repair_canon_assets(
        self,
        repairs: tuple[tuple[uuid.UUID, LandedAsset], ...],
    ) -> tuple[StoredAsset, ...]: ...


class CanonRepairService:
    def __init__(self, *, repository: CanonRepository, asset_store: AssetStore) -> None:
        self._repository = repository
        self._asset_store = asset_store

    def repair_manifest(self, manifest_path: Path) -> tuple[StoredAsset, ...]:
        resolved = manifest_path.expanduser().resolve()
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        entries = payload.get("assets")
        if not isinstance(entries, list):
            raise ValueError("Canon manifest must contain an assets list")
        return self.repair_entries(source_root=resolved.parent, entries=tuple(entries))

    def install_manifest(self, manifest_path: Path) -> tuple[StoredAsset, ...]:
        """Install new immutable Canon assets or verify their exact existing rows."""

        resolved = manifest_path.expanduser().resolve()
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        entries = payload.get("assets")
        if not isinstance(entries, list):
            raise ValueError("Canon manifest must contain an assets list")
        manifest = tuple(CanonManifestEntry.model_validate(item) for item in entries)
        keys = [item.semantic_key for item in manifest]
        if len(keys) != len(set(keys)):
            raise ValueError("Canon manifest contains duplicate semantic keys")

        root = resolved.parent
        installed: list[StoredAsset] = []
        for entry in manifest:
            source = (root / entry.file).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError(f"Canon source file is unavailable: {entry.file}")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != entry.sha256:
                raise ValueError(f"Canon hash mismatch: {entry.semantic_key}")
            landed = self._asset_store.import_local(source)
            if landed.sha256 != digest:
                raise ValueError(f"Canon imported hash mismatch: {entry.semantic_key}")
            installed.append(
                self._repository.install_canon_asset(
                    landed=landed,
                    semantic_key=entry.semantic_key,
                    role=entry.role or entry.group or "canon_reference",
                    display_name=entry.display_name or entry.semantic_key,
                    group=entry.group,
                    recommended_default=entry.recommended_default,
                )
            )
        return tuple(installed)

    def repair_entries(
        self,
        *,
        source_root: Path,
        entries: tuple[dict[str, Any], ...],
    ) -> tuple[StoredAsset, ...]:
        manifest = tuple(CanonManifestEntry.model_validate(item) for item in entries)
        keys = [item.semantic_key for item in manifest]
        if len(keys) != len(set(keys)):
            raise ValueError("Canon manifest contains duplicate semantic keys")
        database_assets = {
            item.semantic_key: item
            for item in self._repository.list_assets()
            if item.scope == "canon" and item.status == "approved" and item.semantic_key
        }
        repairs: list[tuple[uuid.UUID, LandedAsset]] = []
        root = source_root.expanduser().resolve()
        for entry in manifest:
            asset = database_assets.get(entry.semantic_key)
            if asset is None:
                raise ValueError(f"approved Canon asset is missing: {entry.semantic_key}")
            source = (root / entry.file).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError(f"Canon source file is unavailable: {entry.file}")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != entry.sha256 or digest != asset.sha256:
                raise ValueError(f"Canon hash mismatch: {entry.semantic_key}")
            landed = self._asset_store.import_local(source)
            if landed.sha256 != digest:
                raise ValueError(f"Canon imported hash mismatch: {entry.semantic_key}")
            repairs.append((asset.id, landed))
        return self._repository.repair_canon_assets(tuple(repairs))
