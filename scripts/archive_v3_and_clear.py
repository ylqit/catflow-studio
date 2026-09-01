"""Archive V2/V3 audit records, then clear runtime rows before migration 0015.

This script preserves approved Canon and delivered files.  It verifies every
known media hash and writes a manifest plus a manifest SHA-256 before opening
the destructive database transaction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
    load_local_env,
)
from cat_video_generator.infrastructure.db.session import create_database_engine


def main() -> None:
    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = create_database_engine(database, DatabaseOperation.MIGRATION)
    schema = database.schema
    try:
        with engine.connect() as connection:
            available = set(inspect(connection).get_table_names(schema=schema))
            tables = tuple(
                name
                for name in (
                    "production_runs",
                    "episodes",
                    "workflow_steps",
                    "prompt_records",
                    "assets",
                    "reviews",
                    "video_sequences",
                    "delivery_packages",
                    "delivery_items",
                )
                if name in available
            )
            records = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        text(f"SELECT * FROM {schema}.{table}")
                    ).mappings()
                ]
                for table in tables
            }
        delivered_asset_ids = {
            str(row["asset_id"])
            for row in records.get("delivery_items", [])
            if row.get("asset_id") is not None
        }
        deletable_files: list[Path] = []
        media_checks: list[dict[str, Any]] = []
        for row in records.get("assets", []):
            path = Path(str(row["local_path"])).expanduser().resolve()
            exists = path.is_file()
            actual_hash = _sha256(path) if exists else None
            expected_hash = str(row["sha256"])
            media_checks.append(
                {
                    "assetId": str(row["id"]),
                    "path": str(path),
                    "scope": row["scope"],
                    "delivered": str(row["id"]) in delivered_asset_ids,
                    "exists": exists,
                    "expectedSha256": expected_hash,
                    "actualSha256": actual_hash,
                }
            )
            if exists and actual_hash != expected_hash:
                raise RuntimeError(f"asset hash mismatch; refusing cleanup: {path}")
            durable = row["scope"] == "canon" and row["status"] == "approved"
            durable = durable or str(row["id"]) in delivered_asset_ids
            if durable and not exists:
                raise RuntimeError(
                    f"approved Canon or delivered media is missing; refusing cleanup: {path}"
                )
            if (
                (row["scope"] != "canon" or row["status"] != "approved")
                and str(row["id"]) not in delivered_asset_ids
                and exists
                and _inside_media_roots(path, runtime)
            ):
                deletable_files.append(path)
        payload = {
            "createdAt": datetime.now(UTC).isoformat(),
            "database": database.database,
            "schema": schema,
            "targetRevision": "0015_shot_queue_core",
            "recordCounts": {name: len(rows) for name, rows in records.items()},
            "records": _archive_safe(records),
            "mediaChecks": media_checks,
        }
        diagnostics = Path("var/diagnostics").resolve()
        diagnostics.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        manifest = diagnostics / f"v3-archive-before-0015-{stamp}.json"
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        manifest.write_text(content, encoding="utf-8")
        manifest_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        manifest.with_suffix(".sha256").write_text(manifest_hash + "\n", encoding="utf-8")

        with engine.begin() as connection:
            # Old Canon rows should already be global.  Detach them defensively
            # before deleting runs so a historical FK cannot cascade away an
            # approved identity/style asset.
            connection.execute(
                text(
                    f"UPDATE {schema}.assets SET production_run_id=NULL, "
                    "episode_id=NULL, producing_step_id=NULL "
                    "WHERE scope='canon' AND status='approved'"
                )
            )
            # V1 intentionally kept delivery item references restrictive. Delete
            # the archived package contents before the run cascade reaches their
            # episodes; delivered files remain protected by the manifest above.
            if "delivery_items" in available:
                connection.execute(text(f"DELETE FROM {schema}.delivery_items"))
            connection.execute(text(f"DELETE FROM {schema}.production_runs"))
            connection.execute(
                text(f"DELETE FROM {schema}.assets WHERE scope <> 'canon' OR status <> 'approved'")
            )
            remaining = connection.execute(
                text(
                    f"SELECT count(*) FROM {schema}.assets "
                    "WHERE scope='canon' AND status='approved'"
                )
            ).scalar_one()
            invalid = connection.execute(
                text(
                    f"SELECT count(*) FROM {schema}.assets "
                    "WHERE scope <> 'canon' OR status <> 'approved'"
                )
            ).scalar_one()
            if invalid:
                raise RuntimeError("cleanup did not leave an approved-Canon-only asset set")
        for path in sorted(set(deletable_files)):
            path.unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "manifest": str(manifest),
                    "manifestSha256": manifest_hash,
                    "approvedCanonPreserved": remaining,
                    "nonDeliveredMediaRemoved": len(set(deletable_files)),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        engine.dispose()


def _inside_media_roots(path: Path, runtime: RuntimeSettings) -> bool:
    roots = (
        runtime.work_root.expanduser().resolve(),
        runtime.asset_root.expanduser().resolve(),
    )
    return any(path.is_relative_to(root) for root in roots)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime, Decimal, Path)):
        return str(value)
    if hasattr(value, "hex"):
        return str(value)
    return value


def _archive_safe(value: Any, *, key: str = "") -> Any:
    """Keep audit facts while excluding secrets and non-durable provider payloads."""

    normalized_key = key.lower().replace("_", "")
    if any(
        marker in normalized_key
        for marker in ("password", "apikey", "authorization", "accesstoken", "secret")
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(child_key): _archive_safe(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_archive_safe(item, key=key) for item in value]
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered.startswith("data:"):
            return "[base64 omitted]"
        if lowered.startswith(("http://", "https://")):
            return "[provider URL omitted]"
    return _json_safe(value)


if __name__ == "__main__":
    main()
