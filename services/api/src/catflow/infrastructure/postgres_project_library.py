from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.project_library import (
    ProjectCollectionCreate,
    ProjectCollectionDto,
    ProjectCollectionPatch,
    ProjectLibraryBatchActionCommand,
    ProjectLibraryBatchResultDto,
    ProjectLibraryItemDto,
    ProjectLibraryPageDto,
    ProjectLibraryQuery,
    ProjectOrganizationCommand,
    ProjectTagDto,
    decode_library_cursor,
    encode_library_cursor,
    normalize_organization_name,
    normalize_tags,
)
from catflow.application.service import StudioConflictError, StudioNotFoundError

from .models import (
    AssetRecord,
    CanonProfileRecord,
    EditVersionRecord,
    EnvironmentPresetRecord,
    JobRecord,
    ProjectCollectionRecord,
    ProjectRecord,
    ProjectSelectionRecord,
    ProjectTagRecord,
    ShotPlanVersionRecord,
    StoryVersionRecord,
    VideoRepairRecord,
)

ACTIVE_JOB_STATUSES = {
    "queued",
    "submitting",
    "submitted",
    "polling",
    "storing",
    "cancel_requested",
}
PRODUCTION_SLOTS = {
    "episode_child",
    "episode_cat",
    "pair_scale",
    "environment",
    "style_board",
}

LIBRARY_PROJECTION_SQL = """
WITH current_selections AS (
    SELECT DISTINCT ON (selection.project_id, selection.slot)
        selection.project_id,
        selection.slot,
        selection.created_at,
        asset.id AS asset_id,
        asset.sha256
    FROM catflow.project_selections AS selection
    JOIN catflow.assets AS asset ON asset.id = selection.asset_id
    WHERE selection.decision IN ('selected', 'approved')
    ORDER BY
        selection.project_id,
        selection.slot,
        selection.created_at DESC,
        selection.id DESC
),
selection_state AS (
    SELECT
        project_id,
        jsonb_object_agg(
            slot,
            jsonb_build_object('assetId', asset_id::text, 'sha256', sha256)
        ) AS selections
    FROM current_selections
    GROUP BY project_id
),
selection_activity AS (
    SELECT project_id, max(created_at) AS activity_at
    FROM catflow.project_selections
    GROUP BY project_id
),
story_activity AS (
    SELECT project_id, max(created_at) AS activity_at
    FROM catflow.story_versions
    GROUP BY project_id
),
active_story AS (
    SELECT DISTINCT ON (project_id) project_id, id
    FROM catflow.story_versions
    WHERE active = true
    ORDER BY project_id, created_at DESC, id DESC
),
plan_activity AS (
    SELECT project_id, max(created_at) AS activity_at
    FROM catflow.shot_plan_versions
    GROUP BY project_id
),
active_plan AS (
    SELECT DISTINCT ON (project_id)
        project_id,
        id,
        source_story_version_id,
        source_selection_hash
    FROM catflow.shot_plan_versions
    WHERE active = true
    ORDER BY project_id, created_at DESC, id DESC
),
latest_jobs AS (
    SELECT DISTINCT ON (project_id, kind)
        project_id,
        kind,
        status,
        updated_at
    FROM catflow.jobs
    ORDER BY project_id, kind, updated_at DESC, id DESC
),
job_state AS (
    SELECT
        project_id,
        bool_or(status = 'failed') AS has_failure,
        bool_or(status = 'submission_unknown') AS has_submission_unknown,
        bool_or(status IN (
            'queued', 'submitting', 'submitted', 'polling', 'storing', 'cancel_requested'
        )) AS has_running,
        max(updated_at) AS activity_at
    FROM latest_jobs
    GROUP BY project_id
),
edit_state AS (
    SELECT project_id, max(created_at) AS activity_at
    FROM catflow.edit_versions
    GROUP BY project_id
),
repair_state AS (
    SELECT project_id, bool_or(status = 'candidate_ready') AS has_candidate
    FROM catflow.video_repairs
    GROUP BY project_id
),
video_asset_state AS (
    SELECT project_id, true AS has_video_candidate
    FROM catflow.assets
    WHERE project_id IS NOT NULL AND role = 'video'
    GROUP BY project_id
),
tag_state AS (
    SELECT
        project_id,
        jsonb_agg(
            jsonb_build_object('name', name, 'normalizedName', normalized_name)
            ORDER BY created_at, normalized_name
        ) AS tags
    FROM catflow.project_tags
    GROUP BY project_id
),
active_environment AS (
    SELECT preset.asset_id, asset.sha256
    FROM catflow.environment_presets AS preset
    JOIN catflow.assets AS asset ON asset.id = preset.asset_id
    WHERE preset.active = true
    ORDER BY preset.created_at DESC, preset.id DESC
    LIMIT 1
),
asset_candidates AS (
    SELECT
        project.id AS project_id,
        fixed.key AS slot,
        fixed.value ->> 'assetId' AS asset_id,
        fixed.value ->> 'sha256' AS sha256,
        1 AS priority
    FROM catflow.projects AS project
    JOIN catflow.canon_profiles AS profile ON profile.id = project.canon_profile_id
    CROSS JOIN LATERAL jsonb_each(
        coalesce(profile.profile_json -> 'fixedAssets', '{}'::jsonb)
    ) AS fixed
    WHERE fixed.value ? 'assetId' AND fixed.value ? 'sha256'
    UNION ALL
    SELECT
        selection.project_id,
        selection.slot,
        selection.asset_id::text,
        selection.sha256,
        2
    FROM current_selections AS selection
    UNION ALL
    SELECT
        project.id,
        'environment',
        environment.asset_id::text,
        environment.sha256,
        3
    FROM catflow.projects AS project
    CROSS JOIN active_environment AS environment
),
current_assets AS (
    SELECT DISTINCT ON (project_id, slot)
        project_id,
        slot,
        asset_id,
        sha256
    FROM asset_candidates
    ORDER BY project_id, slot, priority DESC
),
current_asset_state AS (
    SELECT
        project_id,
        jsonb_object_agg(
            slot,
            jsonb_build_object('assetId', asset_id, 'sha256', sha256)
        ) AS assets
    FROM current_assets
    GROUP BY project_id
),
production_selection_hash AS (
    SELECT
        project_id,
        encode(
            sha256(
                convert_to(
                    '{' || string_agg(
                        to_json(slot)::text
                        || ':{"assetId":'
                        || to_json(asset_id)::text
                        || ',"sha256":'
                        || to_json(sha256)::text
                        || '}',
                        ',' ORDER BY slot
                    ) || '}',
                    'UTF8'
                )
            ),
            'hex'
        ) AS selection_hash
    FROM current_assets
    WHERE slot IN ('episode_child', 'episode_cat', 'pair_scale', 'environment', 'style_board')
    GROUP BY project_id
),
project_facts AS (
    SELECT
        project.id,
        project.title,
        project.theme,
        project.target_duration_seconds,
        project.aspect_ratio,
        project.created_at,
        project.updated_at,
        project.pinned_at,
        project.archived_at,
        collection.id AS collection_id,
        collection.name AS collection_name,
        collection.color_key AS collection_color_key,
        collection.sort_order AS collection_sort_order,
        collection.archived_at AS collection_archived_at,
        collection.created_at AS collection_created_at,
        collection.updated_at AS collection_updated_at,
        coalesce(tag_state.tags, '[]'::jsonb) AS tags,
        coalesce(current_asset_state.assets, '{}'::jsonb) AS current_assets,
        active_story.id AS active_story_id,
        active_plan.id AS active_plan_id,
        (
            active_plan.id IS NOT NULL
            AND (
                active_story.id IS NULL
                OR active_plan.source_story_version_id IS DISTINCT FROM active_story.id
                OR active_plan.source_selection_hash IS DISTINCT FROM
                    production_selection_hash.selection_hash
            )
        ) AS plan_outdated,
        coalesce(job_state.has_failure, false) AS has_failed_job,
        coalesce(job_state.has_submission_unknown, false) AS has_submission_unknown,
        coalesce(job_state.has_running, false) AS has_running_job,
        coalesce(repair_state.has_candidate, false) AS has_repair_candidate,
        coalesce(video_asset_state.has_video_candidate, false) AS has_video_candidate,
        active_environment.asset_id AS environment_asset_id,
        poster.id AS poster_asset_id,
        greatest(
            project.created_at,
            project.updated_at,
            coalesce(story_activity.activity_at, project.created_at),
            coalesce(plan_activity.activity_at, project.created_at),
            coalesce(selection_activity.activity_at, project.created_at),
            coalesce(job_state.activity_at, project.created_at),
            coalesce(edit_state.activity_at, project.created_at)
        ) AS last_activity_at
    FROM catflow.projects AS project
    LEFT JOIN catflow.project_collections AS collection ON collection.id = project.collection_id
    LEFT JOIN selection_state ON selection_state.project_id = project.id
    LEFT JOIN selection_activity ON selection_activity.project_id = project.id
    LEFT JOIN story_activity ON story_activity.project_id = project.id
    LEFT JOIN active_story ON active_story.project_id = project.id
    LEFT JOIN plan_activity ON plan_activity.project_id = project.id
    LEFT JOIN active_plan ON active_plan.project_id = project.id
    LEFT JOIN production_selection_hash ON production_selection_hash.project_id = project.id
    LEFT JOIN current_asset_state ON current_asset_state.project_id = project.id
    LEFT JOIN job_state ON job_state.project_id = project.id
    LEFT JOIN edit_state ON edit_state.project_id = project.id
    LEFT JOIN repair_state ON repair_state.project_id = project.id
    LEFT JOIN video_asset_state ON video_asset_state.project_id = project.id
    LEFT JOIN tag_state ON tag_state.project_id = project.id
    LEFT JOIN active_environment ON true
    LEFT JOIN LATERAL (
        SELECT candidate.id
        FROM catflow.assets AS candidate
        WHERE
            candidate.project_id = project.id
            AND candidate.role = 'project_poster'
            AND candidate.media_type = 'image'
            AND candidate.metadata_json ->> 'sourceAssetId' = coalesce(
                selection_state.selections -> 'final' ->> 'assetId',
                selection_state.selections -> 'video' ->> 'assetId'
            )
        ORDER BY candidate.created_at DESC, candidate.id DESC
        LIMIT 1
    ) AS poster ON true
),
project_projection AS (
    SELECT
        facts.*,
        CASE
            WHEN active_story_id IS NULL THEN 'story'
            WHEN NOT current_assets ?& ARRAY[
                'episode_child', 'episode_cat', 'pair_scale', 'environment', 'style_board'
            ] THEN 'assets'
            WHEN active_plan_id IS NULL OR plan_outdated THEN 'storyboard'
            WHEN NOT current_assets ? 'video' THEN 'generation'
            WHEN NOT current_assets ? 'final' THEN 'editing'
            ELSE 'completed'
        END AS stage,
        CASE
            WHEN
                has_submission_unknown
                OR has_failed_job
                OR plan_outdated
                OR has_repair_candidate
                OR (NOT current_assets ? 'video' AND has_video_candidate)
            THEN 'needs_attention'
            WHEN has_running_job THEN 'running'
            ELSE 'normal'
        END AS attention,
        array_remove(ARRAY[
            CASE WHEN has_submission_unknown THEN 'submission_unknown' END,
            CASE WHEN has_failed_job THEN 'generation_failed' END,
            CASE WHEN plan_outdated THEN 'storyboard_outdated' END,
            CASE WHEN has_repair_candidate THEN 'edit_candidate_ready' END,
            CASE
                WHEN NOT current_assets ? 'video' AND has_video_candidate
                THEN 'video_candidate_ready'
            END
        ], NULL) AS attention_reasons
    FROM project_facts AS facts
),
filtered AS (
    SELECT projection.*
    FROM project_projection AS projection
    WHERE
        (
            (:system_view = 'archived' AND projection.archived_at IS NOT NULL)
            OR (:system_view <> 'archived' AND projection.archived_at IS NULL)
        )
        AND (
            :q = ''
            OR projection.title ILIKE '%' || :q || '%'
            OR projection.theme ILIKE '%' || :q || '%'
            OR coalesce(projection.collection_name, '') ILIKE '%' || :q || '%'
            OR EXISTS (
                SELECT 1
                FROM catflow.project_tags AS search_tag
                WHERE
                    search_tag.project_id = projection.id
                    AND search_tag.name ILIKE '%' || :q || '%'
            )
        )
        AND (
            :collection_id = ''
            OR projection.collection_id = CAST(NULLIF(:collection_id, '') AS uuid)
        )
        AND (NOT :unassigned OR projection.collection_id IS NULL)
        AND NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(CAST(:tags_json AS jsonb)) AS wanted_tag(name)
            WHERE NOT EXISTS (
                SELECT 1
                FROM catflow.project_tags AS actual_tag
                WHERE
                    actual_tag.project_id = projection.id
                    AND actual_tag.normalized_name = wanted_tag.name
            )
        )
        AND (:stage = '' OR projection.stage = :stage)
        AND (
            :system_view <> 'recent'
            OR projection.last_activity_at >= now() - interval '7 days'
        )
        AND (:system_view <> 'in_progress' OR projection.stage <> 'completed')
        AND (
            :system_view <> 'needs_attention'
            OR projection.attention = 'needs_attention'
        )
        AND (:system_view <> 'completed' OR projection.stage = 'completed')
        AND (:system_view <> 'pinned' OR projection.pinned_at IS NOT NULL)
        AND (
            CAST(:date_from AS timestamptz) IS NULL
            OR projection.last_activity_at >= CAST(:date_from AS timestamptz)
        )
        AND (
            CAST(:date_to AS timestamptz) IS NULL
            OR projection.last_activity_at < CAST(:date_to AS timestamptz)
        )
),
page AS (
    SELECT
        filtered.*,
        row_number() OVER (
            ORDER BY
                (filtered.pinned_at IS NOT NULL) DESC,
                CASE WHEN :sort = 'activity' THEN filtered.last_activity_at END DESC NULLS LAST,
                CASE WHEN :sort = 'created' THEN filtered.created_at END DESC NULLS LAST,
                CASE WHEN :sort = 'title' THEN lower(filtered.title) END ASC NULLS LAST,
                CASE WHEN :sort = 'stage' THEN CASE filtered.stage
                    WHEN 'story' THEN 0
                    WHEN 'assets' THEN 1
                    WHEN 'storyboard' THEN 2
                    WHEN 'generation' THEN 3
                    WHEN 'editing' THEN 4
                    ELSE 5
                END END ASC NULLS LAST,
                CASE WHEN :sort = 'stage' THEN filtered.last_activity_at END DESC NULLS LAST,
                CASE WHEN :sort = 'title' THEN filtered.id::text END ASC NULLS LAST,
                CASE WHEN :sort <> 'title' THEN filtered.id END DESC NULLS LAST
        ) AS page_order
    FROM filtered
    ORDER BY
        (filtered.pinned_at IS NOT NULL) DESC,
        CASE WHEN :sort = 'activity' THEN filtered.last_activity_at END DESC NULLS LAST,
        CASE WHEN :sort = 'created' THEN filtered.created_at END DESC NULLS LAST,
        CASE WHEN :sort = 'title' THEN lower(filtered.title) END ASC NULLS LAST,
        CASE WHEN :sort = 'stage' THEN CASE filtered.stage
            WHEN 'story' THEN 0
            WHEN 'assets' THEN 1
            WHEN 'storyboard' THEN 2
            WHEN 'generation' THEN 3
            WHEN 'editing' THEN 4
            ELSE 5
        END END ASC NULLS LAST,
        CASE WHEN :sort = 'stage' THEN filtered.last_activity_at END DESC NULLS LAST,
        CASE WHEN :sort = 'title' THEN filtered.id::text END ASC NULLS LAST,
        CASE WHEN :sort <> 'title' THEN filtered.id END DESC NULLS LAST
    OFFSET :offset
    LIMIT :limit
),
collection_facets AS (
    SELECT
        projection.collection_id AS id,
        min(projection.collection_name) AS name,
        min(projection.collection_sort_order) AS sort_order,
        count(*) AS count
    FROM project_projection AS projection
    WHERE projection.archived_at IS NULL AND projection.collection_id IS NOT NULL
    GROUP BY projection.collection_id
),
tag_facets AS (
    SELECT
        tag.normalized_name,
        min(tag.name) AS name,
        count(*) AS count
    FROM catflow.project_tags AS tag
    JOIN project_projection AS projection ON projection.id = tag.project_id
    WHERE projection.archived_at IS NULL
    GROUP BY tag.normalized_name
)
SELECT
    coalesce((
        SELECT jsonb_agg(
            jsonb_build_object(
                'id', item.id,
                'title', item.title,
                'themeSummary', left(item.theme, 80),
                'targetDurationSeconds', item.target_duration_seconds,
                'aspectRatio', item.aspect_ratio,
                'coverAssetId', coalesce(item.poster_asset_id, item.environment_asset_id),
                'collection', CASE
                    WHEN item.collection_id IS NULL THEN NULL
                    ELSE jsonb_build_object(
                    'id', item.collection_id,
                    'name', item.collection_name,
                    'colorKey', item.collection_color_key,
                    'sortOrder', item.collection_sort_order,
                    'archived', item.collection_archived_at IS NOT NULL,
                    'createdAt', item.collection_created_at,
                    'updatedAt', item.collection_updated_at
                    )
                END,
                'tags', item.tags,
                'stage', item.stage,
                'attention', item.attention,
                'attentionReasons', to_jsonb(item.attention_reasons),
                'pinned', item.pinned_at IS NOT NULL,
                'archived', item.archived_at IS NOT NULL,
                'lastActivityAt', item.last_activity_at,
                'createdAt', item.created_at
            ) ORDER BY item.page_order
        )
        FROM page AS item
    ), '[]'::jsonb) AS items,
    (SELECT count(*) FROM filtered) AS total,
    jsonb_build_object(
        'all', count(*) FILTER (WHERE archived_at IS NULL),
        'recent', count(*) FILTER (
            WHERE archived_at IS NULL AND last_activity_at >= now() - interval '7 days'
        ),
        'in_progress', count(*) FILTER (
            WHERE archived_at IS NULL AND stage <> 'completed'
        ),
        'needs_attention', count(*) FILTER (
            WHERE archived_at IS NULL AND attention = 'needs_attention'
        ),
        'completed', count(*) FILTER (
            WHERE archived_at IS NULL AND stage = 'completed'
        ),
        'pinned', count(*) FILTER (
            WHERE archived_at IS NULL AND pinned_at IS NOT NULL
        ),
        'archived', count(*) FILTER (WHERE archived_at IS NOT NULL)
    ) AS system_views,
    jsonb_build_object(
        'story', count(*) FILTER (WHERE archived_at IS NULL AND stage = 'story'),
        'assets', count(*) FILTER (WHERE archived_at IS NULL AND stage = 'assets'),
        'storyboard', count(*) FILTER (WHERE archived_at IS NULL AND stage = 'storyboard'),
        'generation', count(*) FILTER (WHERE archived_at IS NULL AND stage = 'generation'),
        'editing', count(*) FILTER (WHERE archived_at IS NULL AND stage = 'editing'),
        'completed', count(*) FILTER (WHERE archived_at IS NULL AND stage = 'completed')
    ) AS stages,
    coalesce((
        SELECT jsonb_agg(
            jsonb_build_object('id', id, 'name', name, 'count', count)
            ORDER BY sort_order, name
        )
        FROM collection_facets
    ), '[]'::jsonb) AS collections,
    coalesce((
        SELECT jsonb_agg(
            jsonb_build_object('name', name, 'count', count)
            ORDER BY count DESC, normalized_name
        )
        FROM tag_facets
    ), '[]'::jsonb) AS tags
FROM project_projection
"""


class PostgresProjectLibraryRepository:
    """Owns the batched read projection and atomic organization commands for the library."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list_project_library(self, query: ProjectLibraryQuery) -> ProjectLibraryPageDto:
        offset = decode_library_cursor(query.cursor)
        wanted_tags = normalize_tags(list(query.tags))
        with self._sessions() as session:
            row = (
                session.execute(
                    text(LIBRARY_PROJECTION_SQL),
                    {
                        "q": (query.q or "").strip(),
                        "system_view": query.system_view,
                        "collection_id": str(query.collection_id) if query.collection_id else "",
                        "unassigned": query.unassigned,
                        "tags_json": json.dumps([tag.normalized_name for tag in wanted_tags]),
                        "stage": query.stage or "",
                        "date_from": query.date_from,
                        "date_to": query.date_to,
                        "sort": query.sort,
                        "offset": offset,
                        "limit": query.limit,
                    },
                )
                .mappings()
                .one()
            )
        items = tuple(ProjectLibraryItemDto.model_validate(item) for item in row["items"])
        total = int(row["total"])
        return ProjectLibraryPageDto(
            items=items,
            nextCursor=(
                encode_library_cursor(offset + len(items)) if offset + len(items) < total else None
            ),
            total=total,
            facets={
                "systemViews": row["system_views"],
                "stages": row["stages"],
                "collections": row["collections"],
                "tags": row["tags"],
            },
        )

    def list_project_collections(
        self, *, include_archived: bool = False
    ) -> list[ProjectCollectionDto]:
        with self._sessions() as session:
            statement = select(ProjectCollectionRecord)
            if not include_archived:
                statement = statement.where(ProjectCollectionRecord.archived_at.is_(None))
            records = session.scalars(
                statement.order_by(
                    ProjectCollectionRecord.sort_order,
                    ProjectCollectionRecord.name,
                )
            ).all()
            return [_collection_dto(record) for record in records]

    def create_project_collection(self, command: ProjectCollectionCreate) -> ProjectCollectionDto:
        name, normalized = normalize_organization_name(command.name, maximum_length=40)
        try:
            with self._sessions.begin() as session:
                maximum_sort_order = session.scalar(
                    select(func.max(ProjectCollectionRecord.sort_order))
                )
                sort_order = (int(maximum_sort_order) if maximum_sort_order is not None else -1) + 1
                record = ProjectCollectionRecord(
                    name=name,
                    normalized_name=normalized,
                    color_key=command.color_key,
                    sort_order=sort_order,
                )
                session.add(record)
                session.flush()
                return _collection_dto(record)
        except IntegrityError as exc:
            raise StudioConflictError("collection name already exists") from exc

    def update_project_collection(
        self, collection_id: uuid.UUID, command: ProjectCollectionPatch
    ) -> ProjectCollectionDto:
        try:
            with self._sessions.begin() as session:
                record = session.scalar(
                    select(ProjectCollectionRecord)
                    .where(ProjectCollectionRecord.id == collection_id)
                    .with_for_update()
                )
                if record is None:
                    raise StudioNotFoundError("project collection not found")
                if command.name is not None:
                    record.name, record.normalized_name = normalize_organization_name(
                        command.name, maximum_length=40
                    )
                if command.color_key is not None:
                    record.color_key = command.color_key
                if command.sort_order is not None:
                    record.sort_order = command.sort_order
                record.updated_at = datetime.now(UTC)
                session.flush()
                return _collection_dto(record)
        except IntegrityError as exc:
            raise StudioConflictError("collection name already exists") from exc

    def set_project_collection_archived(
        self, collection_id: uuid.UUID, *, archived: bool
    ) -> ProjectCollectionDto:
        try:
            with self._sessions.begin() as session:
                record = session.scalar(
                    select(ProjectCollectionRecord)
                    .where(ProjectCollectionRecord.id == collection_id)
                    .with_for_update()
                )
                if record is None:
                    raise StudioNotFoundError("project collection not found")
                now = datetime.now(UTC)
                record.archived_at = now if archived else None
                record.updated_at = now
                if archived:
                    session.execute(
                        update(ProjectRecord)
                        .where(ProjectRecord.collection_id == collection_id)
                        .values(collection_id=None, updated_at=now)
                    )
                session.flush()
                return _collection_dto(record)
        except IntegrityError as exc:
            raise StudioConflictError("collection name already exists") from exc

    def list_project_tags(self, *, query: str | None = None) -> list[dict[str, object]]:
        with self._sessions() as session:
            statement = select(
                ProjectTagRecord.name,
                ProjectTagRecord.normalized_name,
                func.count(ProjectTagRecord.project_id),
            ).group_by(ProjectTagRecord.name, ProjectTagRecord.normalized_name)
            if query and query.strip():
                _, normalized = normalize_organization_name(query, maximum_length=24)
                statement = statement.where(ProjectTagRecord.normalized_name.contains(normalized))
            rows = session.execute(statement).all()
            return [
                {"name": name, "count": int(count)}
                for name, _normalized, count in sorted(
                    rows, key=lambda row: (-int(row[2]), str(row[1]))
                )
            ]

    def organize_project(
        self, project_id: uuid.UUID, command: ProjectOrganizationCommand
    ) -> ProjectLibraryItemDto:
        with self._sessions.begin() as session:
            project = self._locked_projects(session, (project_id,))[0]
            if "collection_id" in command.model_fields_set:
                self._require_active_collection(session, command.collection_id)
            if command.archived:
                self._require_not_running(session, (project_id,))
            now = datetime.now(UTC)
            if "collection_id" in command.model_fields_set:
                project.collection_id = command.collection_id
            if command.tags is not None:
                self._replace_tags(session, project_id, normalize_tags(list(command.tags)))
            if command.pinned is not None:
                project.pinned_at = now if command.pinned else None
            if command.archived is not None:
                project.archived_at = now if command.archived else None
            project.updated_at = now
            session.flush()
            return self._build_items(session, [project])[0]

    def apply_project_library_action(
        self, command: ProjectLibraryBatchActionCommand
    ) -> ProjectLibraryBatchResultDto:
        with self._sessions.begin() as session:
            projects = self._locked_projects(session, command.project_ids)
            if command.action == "move_collection":
                self._require_active_collection(session, command.collection_id)
            if command.action == "archive":
                self._require_not_running(session, command.project_ids)
            normalized_tags = normalize_tags(list(command.tags)) if command.tags else ()
            now = datetime.now(UTC)
            for project in projects:
                if command.action == "move_collection":
                    project.collection_id = command.collection_id
                elif command.action == "add_tags":
                    current = session.scalars(
                        select(ProjectTagRecord).where(ProjectTagRecord.project_id == project.id)
                    ).all()
                    combined = normalize_tags(
                        [*(tag.name for tag in current), *(tag.name for tag in normalized_tags)]
                    )
                    self._replace_tags(session, project.id, combined)
                elif command.action == "remove_tags":
                    removed = {tag.normalized_name for tag in normalized_tags}
                    session.execute(
                        delete(ProjectTagRecord).where(
                            ProjectTagRecord.project_id == project.id,
                            ProjectTagRecord.normalized_name.in_(removed),
                        )
                    )
                elif command.action == "pin":
                    project.pinned_at = now
                elif command.action == "unpin":
                    project.pinned_at = None
                elif command.action == "archive":
                    project.archived_at = now
                elif command.action == "restore":
                    project.archived_at = None
                project.updated_at = now
            session.flush()
            return ProjectLibraryBatchResultDto(updatedCount=len(projects))

    def _build_items(
        self, session: Session, projects: list[ProjectRecord]
    ) -> list[ProjectLibraryItemDto]:
        if not projects:
            return []
        project_ids = [project.id for project in projects]
        collections = {
            record.id: _collection_dto(record)
            for record in session.scalars(select(ProjectCollectionRecord)).all()
        }
        tags: dict[uuid.UUID, list[ProjectTagDto]] = defaultdict(list)
        for record in session.scalars(
            select(ProjectTagRecord)
            .where(ProjectTagRecord.project_id.in_(project_ids))
            .order_by(ProjectTagRecord.created_at, ProjectTagRecord.normalized_name)
        ).all():
            tags[record.project_id].append(
                ProjectTagDto(name=record.name, normalizedName=record.normalized_name)
            )
        stories: dict[uuid.UUID, list[StoryVersionRecord]] = defaultdict(list)
        for record in session.scalars(
            select(StoryVersionRecord).where(StoryVersionRecord.project_id.in_(project_ids))
        ).all():
            stories[record.project_id].append(record)
        plans: dict[uuid.UUID, list[ShotPlanVersionRecord]] = defaultdict(list)
        for record in session.scalars(
            select(ShotPlanVersionRecord).where(ShotPlanVersionRecord.project_id.in_(project_ids))
        ).all():
            plans[record.project_id].append(record)
        selections: dict[uuid.UUID, list[ProjectSelectionRecord]] = defaultdict(list)
        for record in session.scalars(
            select(ProjectSelectionRecord)
            .where(ProjectSelectionRecord.project_id.in_(project_ids))
            .order_by(ProjectSelectionRecord.created_at, ProjectSelectionRecord.id)
        ).all():
            selections[record.project_id].append(record)
        jobs: dict[uuid.UUID, list[JobRecord]] = defaultdict(list)
        for record in session.scalars(
            select(JobRecord).where(JobRecord.project_id.in_(project_ids))
        ).all():
            jobs[record.project_id].append(record)
        edits: dict[uuid.UUID, list[EditVersionRecord]] = defaultdict(list)
        for record in session.scalars(
            select(EditVersionRecord).where(EditVersionRecord.project_id.in_(project_ids))
        ).all():
            edits[record.project_id].append(record)
        repairs: dict[uuid.UUID, list[VideoRepairRecord]] = defaultdict(list)
        for record in session.scalars(
            select(VideoRepairRecord).where(VideoRepairRecord.project_id.in_(project_ids))
        ).all():
            repairs[record.project_id].append(record)
        profiles = {
            record.id: record
            for record in session.scalars(
                select(CanonProfileRecord).where(
                    CanonProfileRecord.id.in_({project.canon_profile_id for project in projects})
                )
            ).all()
        }
        asset_ids = {
            selection.asset_id
            for project_selections in selections.values()
            for selection in project_selections
        }
        active_environment = session.scalar(
            select(EnvironmentPresetRecord)
            .where(EnvironmentPresetRecord.active.is_(True))
            .order_by(EnvironmentPresetRecord.created_at.desc())
            .limit(1)
        )
        if active_environment is not None:
            asset_ids.add(active_environment.asset_id)
        assets = (
            {
                record.id: record
                for record in session.scalars(
                    select(AssetRecord).where(AssetRecord.id.in_(asset_ids))
                ).all()
            }
            if asset_ids
            else {}
        )
        posters_by_source: dict[uuid.UUID, AssetRecord] = {}
        for record in session.scalars(
            select(AssetRecord)
            .where(
                AssetRecord.project_id.in_(project_ids),
                AssetRecord.role == "project_poster",
                AssetRecord.media_type == "image",
            )
            .order_by(AssetRecord.created_at, AssetRecord.id)
        ).all():
            source_asset_id = record.metadata_json.get("sourceAssetId")
            if source_asset_id:
                try:
                    posters_by_source[uuid.UUID(str(source_asset_id))] = record
                except ValueError:
                    continue
        video_candidate_projects = set(
            session.scalars(
                select(AssetRecord.project_id).where(
                    AssetRecord.project_id.in_(project_ids),
                    AssetRecord.role == "video",
                )
            ).all()
        )

        result: list[ProjectLibraryItemDto] = []
        for project in projects:
            project_stories = stories[project.id]
            active_story = next((item for item in project_stories if item.active), None)
            project_plans = plans[project.id]
            active_plan = next((item for item in project_plans if item.active), None)
            current_assets: dict[str, tuple[uuid.UUID, str]] = {}
            profile = profiles.get(project.canon_profile_id)
            if profile is not None:
                for role, document in profile.profile_json.get("fixedAssets", {}).items():
                    current_assets[role] = (
                        uuid.UUID(str(document["assetId"])),
                        str(document["sha256"]),
                    )
            for selection in selections[project.id]:
                if selection.decision in {"selected", "approved"}:
                    asset = assets.get(selection.asset_id)
                    if asset is not None:
                        current_assets[selection.slot] = (asset.id, asset.sha256)
            if active_environment is not None:
                environment_asset = assets.get(active_environment.asset_id)
                if environment_asset is not None:
                    current_assets["environment"] = (
                        environment_asset.id,
                        environment_asset.sha256,
                    )
            selection_hash = _selection_hash(current_assets)
            plan_outdated = active_plan is not None and (
                active_story is None
                or active_plan.source_story_version_id != active_story.id
                or active_plan.source_selection_hash != selection_hash
            )
            if active_story is None:
                stage = "story"
            elif not current_assets.keys() >= PRODUCTION_SLOTS:
                stage = "assets"
            elif active_plan is None or plan_outdated:
                stage = "storyboard"
            elif "video" not in current_assets:
                stage = "generation"
            elif "final" not in current_assets:
                stage = "editing"
            else:
                stage = "completed"

            latest_by_kind: dict[str, JobRecord] = {}
            for job in sorted(jobs[project.id], key=lambda item: item.updated_at):
                latest_by_kind[job.kind] = job
            reasons: list[str] = []
            if any(
                job.status in {"failed", "submission_unknown"} for job in latest_by_kind.values()
            ):
                reasons.append("generation_failed")
            if plan_outdated:
                reasons.append("storyboard_outdated")
            if any(repair.status == "candidate_ready" for repair in repairs[project.id]):
                reasons.append("edit_candidate_ready")
            if "video" not in current_assets and project.id in video_candidate_projects:
                reasons.append("video_candidate_ready")
            if reasons:
                attention = "needs_attention"
            elif any(job.status in ACTIVE_JOB_STATUSES for job in latest_by_kind.values()):
                attention = "running"
            else:
                attention = "normal"

            activity_times = [project.created_at, project.updated_at]
            activity_times.extend(item.created_at for item in project_stories)
            activity_times.extend(item.created_at for item in project_plans)
            activity_times.extend(item.created_at for item in selections[project.id])
            activity_times.extend(item.updated_at for item in jobs[project.id])
            activity_times.extend(item.created_at for item in edits[project.id])
            preferred_cover_source = current_assets.get("final") or current_assets.get("video")
            poster = (
                posters_by_source.get(preferred_cover_source[0])
                if preferred_cover_source is not None
                else None
            )
            environment = current_assets.get("environment")
            cover_asset_id = (
                poster.id if poster is not None else (environment[0] if environment else None)
            )
            result.append(
                ProjectLibraryItemDto(
                    id=project.id,
                    title=project.title,
                    themeSummary=project.theme[:80],
                    targetDurationSeconds=project.target_duration_seconds,
                    aspectRatio=project.aspect_ratio,
                    coverAssetId=cover_asset_id,
                    collection=collections.get(project.collection_id),
                    tags=tuple(tags[project.id]),
                    stage=stage,
                    attention=attention,
                    attentionReasons=tuple(reasons),
                    pinned=project.pinned_at is not None,
                    archived=project.archived_at is not None,
                    lastActivityAt=max(activity_times),
                    createdAt=project.created_at,
                    search_text=project.theme,
                )
            )
        return result

    def _locked_projects(
        self, session: Session, project_ids: tuple[uuid.UUID, ...]
    ) -> list[ProjectRecord]:
        projects = session.scalars(
            select(ProjectRecord)
            .where(ProjectRecord.id.in_(project_ids))
            .order_by(ProjectRecord.id)
            .with_for_update()
        ).all()
        if len(projects) != len(project_ids):
            raise StudioNotFoundError("project not found")
        return projects

    def _require_active_collection(self, session: Session, collection_id: uuid.UUID | None) -> None:
        if collection_id is None:
            return
        collection = session.scalar(
            select(ProjectCollectionRecord).where(
                ProjectCollectionRecord.id == collection_id,
                ProjectCollectionRecord.archived_at.is_(None),
            )
        )
        if collection is None:
            raise StudioNotFoundError("project collection not found")

    def _require_not_running(self, session: Session, project_ids: tuple[uuid.UUID, ...]) -> None:
        running = session.scalar(
            select(JobRecord.id)
            .where(
                JobRecord.project_id.in_(project_ids),
                JobRecord.status.in_(ACTIVE_JOB_STATUSES),
            )
            .limit(1)
        )
        if running is not None:
            raise StudioConflictError("running projects cannot be archived")

    def _replace_tags(
        self, session: Session, project_id: uuid.UUID, tags: tuple[ProjectTagDto, ...]
    ) -> None:
        session.execute(delete(ProjectTagRecord).where(ProjectTagRecord.project_id == project_id))
        session.add_all(
            ProjectTagRecord(
                project_id=project_id,
                name=tag.name,
                normalized_name=tag.normalized_name,
            )
            for tag in tags
        )


def _collection_dto(record: ProjectCollectionRecord) -> ProjectCollectionDto:
    return ProjectCollectionDto(
        id=record.id,
        name=record.name,
        colorKey=record.color_key,
        sortOrder=record.sort_order,
        archived=record.archived_at is not None,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _selection_hash(current_assets: dict[str, tuple[uuid.UUID, str]]) -> str:
    document = {
        slot: {"assetId": str(asset_id), "sha256": sha256}
        for slot, (asset_id, sha256) in sorted(current_assets.items())
        if slot in PRODUCTION_SLOTS
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
