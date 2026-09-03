from __future__ import annotations

import base64
import json
import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from catflow.domain.contract import ContractModel

ProjectStage = Literal["story", "assets", "storyboard", "generation", "editing", "completed"]
ProjectAttention = Literal["normal", "running", "needs_attention"]
ProjectSystemView = Literal[
    "all", "recent", "in_progress", "needs_attention", "completed", "pinned", "archived"
]
ProjectLibrarySort = Literal["activity", "created", "title", "stage"]
CollectionColorKey = Literal["clay", "sage", "sky", "lavender", "sand", "rose"]


class ProjectTagDto(ContractModel):
    name: str
    normalized_name: str = Field(alias="normalizedName")


class ProjectCollectionCreate(ContractModel):
    name: str = Field(min_length=1, max_length=40)
    color_key: CollectionColorKey = Field(alias="colorKey", default="clay")


class ProjectCollectionPatch(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    color_key: CollectionColorKey | None = Field(alias="colorKey", default=None)
    sort_order: int | None = Field(alias="sortOrder", default=None, ge=0)

    @model_validator(mode="after")
    def require_change(self) -> ProjectCollectionPatch:
        if self.name is None and self.color_key is None and self.sort_order is None:
            raise ValueError("at least one collection field is required")
        return self


class ProjectCollectionDto(ContractModel):
    id: uuid.UUID
    name: str
    color_key: CollectionColorKey = Field(alias="colorKey")
    sort_order: int = Field(alias="sortOrder")
    archived: bool
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ProjectOrganizationCommand(ContractModel):
    collection_id: uuid.UUID | None = Field(alias="collectionId", default=None)
    tags: tuple[str, ...] | None = None
    pinned: bool | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> ProjectOrganizationCommand:
        if (
            "collection_id" not in self.model_fields_set
            and self.tags is None
            and self.pinned is None
            and self.archived is None
        ):
            raise ValueError("at least one organization field is required")
        return self


ProjectLibraryAction = Literal[
    "move_collection", "add_tags", "remove_tags", "pin", "unpin", "archive", "restore"
]


class ProjectLibraryBatchActionCommand(ContractModel):
    action: ProjectLibraryAction
    project_ids: tuple[uuid.UUID, ...] = Field(alias="projectIds", min_length=1, max_length=200)
    collection_id: uuid.UUID | None = Field(alias="collectionId", default=None)
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_action_payload(self) -> ProjectLibraryBatchActionCommand:
        if self.action in {"add_tags", "remove_tags"} and not self.tags:
            raise ValueError("tags are required for this action")
        if self.action not in {"add_tags", "remove_tags"} and self.tags:
            raise ValueError("tags are only accepted for tag actions")
        if self.action == "move_collection" and "collection_id" not in self.model_fields_set:
            raise ValueError("collectionId is required for move_collection")
        if self.action != "move_collection" and "collection_id" in self.model_fields_set:
            raise ValueError("collectionId is only accepted for move_collection")
        if len(set(self.project_ids)) != len(self.project_ids):
            raise ValueError("projectIds must be unique")
        return self


class ProjectLibraryBatchResultDto(ContractModel):
    updated_count: int = Field(alias="updatedCount")


class ProjectLibraryItemDto(ContractModel):
    id: uuid.UUID
    title: str
    theme_summary: str = Field(alias="themeSummary")
    target_duration_seconds: int = Field(alias="targetDurationSeconds")
    aspect_ratio: str = Field(alias="aspectRatio")
    cover_asset_id: uuid.UUID | None = Field(alias="coverAssetId", default=None)
    collection: ProjectCollectionDto | None = None
    tags: tuple[ProjectTagDto, ...] = ()
    stage: ProjectStage
    attention: ProjectAttention
    attention_reasons: tuple[str, ...] = Field(alias="attentionReasons", default=())
    pinned: bool
    archived: bool
    last_activity_at: datetime = Field(alias="lastActivityAt")
    created_at: datetime = Field(alias="createdAt")
    search_text: str = Field(default="", exclude=True)


class ProjectLibraryFacetsDto(ContractModel):
    system_views: dict[str, int] = Field(alias="systemViews")
    stages: dict[str, int]
    collections: tuple[dict[str, object], ...]
    tags: tuple[dict[str, object], ...]


class ProjectLibraryPageDto(ContractModel):
    items: tuple[ProjectLibraryItemDto, ...]
    next_cursor: str | None = Field(alias="nextCursor", default=None)
    total: int
    facets: ProjectLibraryFacetsDto


class ProjectLibraryQuery(ContractModel):
    q: str | None = Field(default=None, max_length=200)
    system_view: ProjectSystemView = Field(alias="systemView", default="all")
    collection_id: uuid.UUID | None = Field(alias="collectionId", default=None)
    unassigned: bool = False
    tags: tuple[str, ...] = ()
    stage: ProjectStage | None = None
    date_from: datetime | None = Field(alias="dateFrom", default=None)
    date_to: datetime | None = Field(alias="dateTo", default=None)
    sort: ProjectLibrarySort = "activity"
    cursor: str | None = None
    limit: int = Field(default=36, ge=12, le=60)


@runtime_checkable
class ProjectLibraryRepository(Protocol):
    def list_project_library(self, query: ProjectLibraryQuery) -> ProjectLibraryPageDto: ...

    def list_project_collections(
        self, *, include_archived: bool = False
    ) -> list[ProjectCollectionDto]: ...

    def create_project_collection(
        self, command: ProjectCollectionCreate
    ) -> ProjectCollectionDto: ...

    def update_project_collection(
        self, collection_id: uuid.UUID, command: ProjectCollectionPatch
    ) -> ProjectCollectionDto: ...

    def set_project_collection_archived(
        self, collection_id: uuid.UUID, *, archived: bool
    ) -> ProjectCollectionDto: ...

    def list_project_tags(self, *, query: str | None = None) -> list[dict[str, object]]: ...

    def organize_project(
        self, project_id: uuid.UUID, command: ProjectOrganizationCommand
    ) -> ProjectLibraryItemDto: ...

    def apply_project_library_action(
        self, command: ProjectLibraryBatchActionCommand
    ) -> ProjectLibraryBatchResultDto: ...


def normalize_organization_name(value: str, *, maximum_length: int) -> tuple[str, str]:
    display = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip())
    if not display:
        raise ValueError("name cannot be blank")
    if len(display) > maximum_length:
        raise ValueError(f"name cannot exceed {maximum_length} characters")
    normalized = display.casefold()
    if len(normalized) > maximum_length:
        raise ValueError(f"normalized name cannot exceed {maximum_length} characters")
    return display, normalized


def normalize_tags(values: tuple[str, ...] | list[str]) -> tuple[ProjectTagDto, ...]:
    normalized: dict[str, ProjectTagDto] = {}
    for value in values:
        name, key = normalize_organization_name(value, maximum_length=24)
        normalized.setdefault(key, ProjectTagDto(name=name, normalizedName=key))
    if len(normalized) > 8:
        raise ValueError("a project can have at most 8 tags")
    return tuple(normalized.values())


def suggested_theme_tags(theme: str) -> tuple[ProjectTagDto, ...]:
    try:
        return normalize_tags([theme])
    except ValueError:
        return ()


def encode_library_cursor(offset: int) -> str:
    payload = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_library_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        payload = cursor + "=" * (-len(cursor) % 4)
        document = json.loads(base64.urlsafe_b64decode(payload).decode())
        offset = int(document["offset"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid project library cursor") from exc
    if offset < 0:
        raise ValueError("invalid project library cursor")
    return offset


def project_library_page(
    all_items: list[ProjectLibraryItemDto], query: ProjectLibraryQuery
) -> ProjectLibraryPageDto:
    items = _filter_library_items(all_items, query)
    _sort_library_items(items, query.sort)
    offset = decode_library_cursor(query.cursor)
    page_items = tuple(items[offset : offset + query.limit])
    next_cursor = (
        encode_library_cursor(offset + query.limit) if offset + query.limit < len(items) else None
    )
    return ProjectLibraryPageDto(
        items=page_items,
        nextCursor=next_cursor,
        total=len(items),
        facets=project_library_facets(all_items),
    )


def project_library_facets(items: list[ProjectLibraryItemDto]) -> ProjectLibraryFacetsDto:
    active = [item for item in items if not item.archived]
    collections: dict[uuid.UUID, tuple[ProjectCollectionDto, int]] = {}
    tags: dict[str, tuple[str, int]] = {}
    for item in active:
        if item.collection is not None:
            collection, count = collections.get(item.collection.id, (item.collection, 0))
            collections[item.collection.id] = (collection, count + 1)
        for tag in item.tags:
            name, count = tags.get(tag.normalized_name, (tag.name, 0))
            tags[tag.normalized_name] = (name, count + 1)
    recent_boundary = datetime.now(UTC) - timedelta(days=7)
    return ProjectLibraryFacetsDto(
        systemViews={
            "all": len(active),
            "recent": sum(item.last_activity_at >= recent_boundary for item in active),
            "in_progress": sum(item.stage != "completed" for item in active),
            "needs_attention": sum(item.attention == "needs_attention" for item in active),
            "completed": sum(item.stage == "completed" for item in active),
            "pinned": sum(item.pinned for item in active),
            "archived": len(items) - len(active),
        },
        stages={
            stage: sum(item.stage == stage for item in active)
            for stage in ("story", "assets", "storyboard", "generation", "editing", "completed")
        },
        collections=tuple(
            {"id": str(collection.id), "name": collection.name, "count": count}
            for collection, count in sorted(
                collections.values(), key=lambda pair: (pair[0].sort_order, pair[0].name)
            )
        ),
        tags=tuple(
            {"name": name, "count": count}
            for _, (name, count) in sorted(tags.items(), key=lambda pair: (-pair[1][1], pair[0]))
        ),
    )


def _filter_library_items(
    items: list[ProjectLibraryItemDto], query: ProjectLibraryQuery
) -> list[ProjectLibraryItemDto]:
    normalized_query = (query.q or "").strip().casefold()
    wanted_tags = {tag.normalized_name for tag in normalize_tags(list(query.tags))}
    if query.system_view == "archived":
        result = [item for item in items if item.archived]
    else:
        result = [item for item in items if not item.archived]
    if normalized_query:
        result = [
            item
            for item in result
            if normalized_query
            in " ".join(
                (
                    item.title,
                    item.search_text or item.theme_summary,
                    item.collection.name if item.collection else "",
                    *(tag.name for tag in item.tags),
                )
            ).casefold()
        ]
    if query.collection_id is not None:
        result = [
            item
            for item in result
            if item.collection is not None and item.collection.id == query.collection_id
        ]
    elif query.unassigned:
        result = [item for item in result if item.collection is None]
    if wanted_tags:
        result = [
            item for item in result if wanted_tags <= {tag.normalized_name for tag in item.tags}
        ]
    if query.stage is not None:
        result = [item for item in result if item.stage == query.stage]
    recent_boundary = datetime.now(UTC) - timedelta(days=7)
    if query.system_view == "recent":
        result = [item for item in result if item.last_activity_at >= recent_boundary]
    elif query.system_view == "in_progress":
        result = [item for item in result if item.stage != "completed"]
    elif query.system_view == "needs_attention":
        result = [item for item in result if item.attention == "needs_attention"]
    elif query.system_view == "completed":
        result = [item for item in result if item.stage == "completed"]
    elif query.system_view == "pinned":
        result = [item for item in result if item.pinned]
    if query.date_from is not None:
        result = [item for item in result if item.last_activity_at >= query.date_from]
    if query.date_to is not None:
        result = [item for item in result if item.last_activity_at < query.date_to]
    return result


def _sort_library_items(items: list[ProjectLibraryItemDto], sort: ProjectLibrarySort) -> None:
    stage_order = {
        stage: index
        for index, stage in enumerate(
            ("story", "assets", "storyboard", "generation", "editing", "completed")
        )
    }
    if sort == "title":
        items.sort(key=lambda item: (not item.pinned, item.title.casefold(), str(item.id)))
    elif sort == "created":
        items.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        items.sort(key=lambda item: not item.pinned)
    elif sort == "stage":
        items.sort(
            key=lambda item: (
                not item.pinned,
                stage_order[item.stage],
                -item.last_activity_at.timestamp(),
                str(item.id),
            )
        )
    else:
        items.sort(key=lambda item: (item.last_activity_at, str(item.id)), reverse=True)
        items.sort(key=lambda item: not item.pinned)
