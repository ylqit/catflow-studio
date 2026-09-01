from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import Field

from .contract import ContractModel

ReferenceRole = Literal[
    "episode_child",
    "episode_cat",
    "pair_scale",
    "environment",
    "style_board",
    "style_source",
]

REFERENCE_PRIORITY: dict[str, int] = {
    "episode_child": 10,
    "episode_cat": 20,
    "pair_scale": 30,
    "environment": 40,
    "style_board": 50,
    "style_source": 60,
}


class ProviderReference(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    role: ReferenceRole
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompiledReference(ProviderReference):
    priority: int
    included: bool
    omitted_reason: str | None = Field(alias="omittedReason", default=None)


class CompiledReferenceSet(ContractModel):
    references: list[CompiledReference]
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")


def compile_references(
    references: list[ProviderReference], *, maximum_references: int
) -> CompiledReferenceSet:
    if maximum_references < 0:
        raise ValueError("maximum_references must not be negative")
    role_counts: dict[str, int] = {}
    for reference in references:
        role_counts[reference.role] = role_counts.get(reference.role, 0) + 1
    duplicate_roles = sorted(role for role, count in role_counts.items() if count > 1)
    if duplicate_roles:
        raise ValueError(f"duplicate reference roles: {', '.join(duplicate_roles)}")

    ordered = sorted(references, key=lambda item: REFERENCE_PRIORITY[item.role])
    compiled: list[CompiledReference] = []
    included_count = 0
    for reference in ordered:
        if reference.role == "style_source":
            included = False
            omitted_reason = "style_source_not_provider_eligible"
        elif included_count >= maximum_references:
            included = False
            omitted_reason = "provider_reference_limit"
        else:
            included = True
            included_count += 1
            omitted_reason = None
        compiled.append(
            CompiledReference(
                **reference.model_dump(by_alias=True),
                priority=REFERENCE_PRIORITY[reference.role],
                included=included,
                omittedReason=omitted_reason,
            )
        )

    document = [item.model_dump(mode="json", by_alias=True) for item in compiled]
    input_hash = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CompiledReferenceSet(references=compiled, inputHash=input_hash)
