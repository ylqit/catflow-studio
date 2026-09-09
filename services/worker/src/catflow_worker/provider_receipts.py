"""Local write-ahead receipts survive a DB outage and never re-submit a request."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError

LOGGER = logging.getLogger(__name__)


def receipt_document(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        value = vars(value)
    if isinstance(value, dict):
        return {
            str(k): receipt_document(v)
            for k, v in value.items()
            if str(k).lower().replace("-", "_")
            not in {
                "authorization",
                "api_key",
                "apikey",
                "access_token",
                "secret",
                "credentials",
            }
            and not str(k).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [receipt_document(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class ReceiptJournal:
    def __init__(self, root: Path):
        self.root = root

    def append(self, job_id: uuid.UUID, document: dict[str, Any]) -> dict[str, Any]:
        directory = self.root / str(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        received = datetime.now(UTC).isoformat()
        data = json.dumps(
            {"jobId": str(job_id), "receivedAt": received, "document": receipt_document(document)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        path = directory / f"{identifier}.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return {
            **json.loads(data),
            "reference": {
                "id": identifier,
                "path": f"{job_id}/{path.name}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "receivedAt": received,
            },
        }

    def read(self, job_id: uuid.UUID) -> list[dict[str, Any]]:
        receipts = []
        for path in (self.root / str(job_id)).glob("*.json"):
            data = path.read_bytes()
            item = json.loads(data)
            if item.get("jobId") != str(job_id):
                raise ValueError("receipt job identity mismatch")
            item["reference"] = {
                "id": path.stem,
                "path": f"{job_id}/{path.name}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "receivedAt": item["receivedAt"],
            }
            receipts.append(item)
        return sorted(receipts, key=lambda item: item["receivedAt"])


@dataclass
class ProviderCall:
    job_id: uuid.UUID
    contract: dict[str, Any]
    journal: ReceiptJournal
    register: Callable[[dict[str, Any]], None]

    def receive(self, document: dict[str, Any]) -> None:
        receipt = self.journal.append(self.job_id, document)
        try:
            self.register(receipt)
        except SQLAlchemyError:
            # File is durable. Let the receiver continue collecting remote evidence.
            LOGGER.exception("receipt_registration_deferred job_id=%s", self.job_id)


provider_call: ContextVar[ProviderCall | None] = ContextVar("provider_call", default=None)


def receive_receipt(document: dict[str, Any]) -> None:
    call = provider_call.get()
    if call is not None:
        call.receive(document)


def read_http_receipt(raw) -> dict[str, Any]:
    """Keep tracking and malformed HTTP bodies before SDK parsing can discard them."""
    receive_receipt({"serverRequestId": raw.headers.get("x-request-id")})
    try:
        document = raw.json()
    except ValueError:
        receive_receipt({"result": {"rawText": raw.text()}})
        raise
    if not isinstance(document, dict):
        receive_receipt({"result": {"rawText": raw.text()}})
        raise ValueError("外部接口返回的 JSON 不是对象；实际正文已保存。")
    return document
