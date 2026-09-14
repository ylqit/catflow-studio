"""Memory implementation of the same binding and remake ownership rules."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps

from catflow.application.character_references import (
    CharacterRemakeDto,
    ReferenceBindingDto,
    remake_preview,
    remake_request_hash,
)
from catflow.application.series import SeriesCreateCommand
from catflow.application.service import (
    FIXED_CANON_ROLES,
    AuxiliaryCatReferenceDto,
    CatReferenceOptionDto,
    ProjectCreate,
    StudioConflictError,
    StudioIdempotencyInputConflictError,
    StudioNotFoundError,
)


def reference_transaction(method):
    @wraps(method)
    def execute(self, *args, **kwargs):
        with self._reference_lock:
            return method(self, *args, **kwargs)

    return execute


class MemoryCharacterReferences:
    def list_canon_profiles(self):
        return list(reversed(self._canon_profiles))

    def save_cat_reference_option(self, **values):
        self._cat_options[values["key"]] = values

    def list_cat_reference_options(self):
        options = []
        for value in sorted(self._cat_options.values(), key=lambda v: v["sort_order"]):
            canon = (
                self.get_canon_profile(value["canon_profile_id"])
                if value["canon_profile_id"]
                else None
            )
            options.append(
                CatReferenceOptionDto(
                    key=value["key"],
                    label=value["label"],
                    canonProfileId=canon.id if canon else None,
                    profileHash=canon.profile_hash if canon else None,
                    version=canon.version if canon else None,
                    fixedAssets=canon.fixed_assets if canon else {},
                    catIdentity=canon.cat_identity_prompt if canon else "",
                    available=canon is not None and not value.get("unavailable_reason"),
                    unavailableReason=value.get("unavailable_reason"),
                    auxiliary=[
                        AuxiliaryCatReferenceDto(
                            view=a["view"], asset=self._assets[uuid.UUID(a["assetId"])]
                        )
                        for a in value["auxiliary"]
                    ],
                )
            )
        return options

    def _reference_owners(self, scope, object_id):
        series_id = (
            object_id
            if scope == "series"
            else next(
                (
                    sid
                    for sid, episodes in self._series_episodes.items()
                    if any(e.project_id == object_id for e in episodes)
                ),
                None,
            )
        )
        if series_id is not None:
            series = self._story_series.get(series_id)
            if series is None:
                raise StudioNotFoundError("series not found")
            projects = [
                self._projects[e.project_id]
                for e in self._series_episodes[series_id]
                if e.project_id
            ]
        else:
            series = None
            project = self._projects.get(object_id)
            if project is None:
                raise StudioNotFoundError("project not found")
            projects = [project]
        return series, projects

    @reference_transaction
    def get_reference_binding(self, scope, object_id):
        series, projects = self._reference_owners(scope, object_id)
        owner = series if scope == "series" else self._projects[object_id]
        ids = {p.id for p in projects}
        dates = [
            date
            for (kind, oid), date in self._production_started.items()
            if (kind == "project" and oid in ids)
            or (series and kind == "series" and oid == series.id)
        ]
        dates.extend(
            j.created_at
            for j in self._jobs.values()
            if j.project_id in ids or (series and j.series_id == series.id)
        )
        produced = any(
            self._stories.get(pid)
            or self._shot_plans.get(pid)
            or any(a.project_id == pid for a in self._assets.values())
            for pid in ids
        )
        produced = produced or (series and self._series_plans.get(series.id))
        started = min(dates) if dates else owner.created_at if produced else None
        canon = self.get_canon_profile(owner.canon_profile_id)
        option = next(
            (v for v in self._cat_options.values() if v["canon_profile_id"] == canon.id), None
        )
        inherited = scope == "project" and series is not None
        return ReferenceBindingDto(
            scope=scope,
            objectId=object_id,
            canonProfileId=canon.id,
            label=option["label"] if option else "历史参考设定",
            catIdentity=canon.cat_identity_prompt,
            canChange=not inherited and not started,
            ownerSeriesId=series.id if inherited else None,
            productionStartedAt=started,
            blockedReason="单集继承本系列的猫咪参考。"
            if inherited
            else "已经开始制作，请用其他猫咪重制。"
            if started
            else None,
        )

    @reference_transaction
    def change_reference_binding(self, scope, object_id, command):
        binding = self.get_reference_binding(scope, object_id)
        if binding.owner_series_id:
            raise StudioConflictError("单集继承本系列的猫咪参考，不能独立更换。")
        if binding.canon_profile_id == command.canon_profile_id:
            return
        if not binding.can_change:
            raise StudioConflictError(binding.blocked_reason)
        if binding.canon_profile_id != command.expected_canon_profile_id:
            raise StudioConflictError("猫咪参考已变化，请刷新后重试。")
        canon = self.get_canon_profile(command.canon_profile_id)
        if set(canon.fixed_assets) != set(FIXED_CANON_ROLES):
            raise StudioConflictError("目标参考不完整。")
        series, projects = self._reference_owners(scope, object_id)
        change = {"canon_profile_id": canon.id, "updated_at": datetime.now(UTC)}
        if series:
            self._story_series[series.id] = series.model_copy(update=change)
        for project in projects:
            self._projects[project.id] = project.model_copy(update=change)

    def _mark_reference_production(self, job):
        if job.project_id is None and job.series_id is None:
            return
        scope, oid = ("project", job.project_id) if job.project_id else ("series", job.series_id)
        series, projects = self._reference_owners(scope, oid)
        owner = series or projects[0]
        expected = job.frozen_input.get("canonProfileId")
        if expected is not None and str(owner.canon_profile_id) != str(expected):
            raise StudioConflictError("猫咪参考已变化，请重新预览后提交。")
        if (
            job.project_id
            and self._projects[job.project_id].canon_profile_id != owner.canon_profile_id
        ):
            raise StudioConflictError("单集参考与系列不一致。")
        if series:
            self._production_started.setdefault(("series", series.id), datetime.now(UTC))
        self._production_started.setdefault((scope, oid), datetime.now(UTC))

    def character_remake_source(self, scope, object_id):
        owner = (
            self._story_series.get(object_id)
            if scope == "series"
            else self._projects.get(object_id)
        )
        if owner is None:
            raise StudioNotFoundError("remake source not found")
        fields = (
            set(SeriesCreateCommand.model_fields) - {"canon_profile_id"}
            if scope == "series"
            else {"title", "theme", "target_duration_seconds"}
        )
        sources, messages = [], []
        if scope == "series":
            sources = [
                {
                    "unitId": str(b.source_unit_id),
                    "title": b.title,
                    "rawText": b.raw_text,
                    "ordinal": b.source_unit_ordinal,
                    "bindingOrder": b.binding_order,
                }
                for b in self._series_source_beats.get(object_id, [])
            ]
        else:
            messages = [
                m.content
                for m in self._messages.get(self._planner_sessions[object_id][0], [])
                if m.role == "user"
            ]
            for material in self._story_source_materializations.values():
                if object_id != material.target_project_id and not any(
                    p.id == object_id for p in material.projects
                ):
                    continue
                for doc in self._story_source_documents.values():
                    suggestion = next(
                        (s for s in doc.relation_suggestions if s.id == material.suggestion_id),
                        None,
                    )
                    if suggestion:
                        sources.extend(
                            {
                                "unitId": str(u.id),
                                "title": u.title,
                                "rawText": u.raw_text,
                                "ordinal": u.ordinal,
                            }
                            for u in doc.units
                            if u.id in suggestion.unit_ids
                        )
            episode = next(
                (
                    e
                    for episodes in self._series_episodes.values()
                    for e in episodes
                    if e.project_id == object_id
                ),
                None,
            )
            if episode:
                ordinals = {c.source_unit_ordinal for c in episode.outline.source_coverage}
                sources.extend(
                    {
                        "unitId": str(b.source_unit_id),
                        "title": b.title,
                        "rawText": b.raw_text,
                        "ordinal": b.source_unit_ordinal,
                    }
                    for b in self._series_source_beats.get(episode.series_id, [])
                    if b.binding_order in ordinals
                )
            prior = self.remake_input_context(object_id)
            if prior:
                sources = prior.get("sources", []) + sources
                messages = prior.get("userMessages", []) + messages
        return {
            "input": owner.model_dump(mode="json", by_alias=True, include=fields),
            "sources": sources,
            "userMessages": messages,
            "sourceCanonProfileId": str(owner.canon_profile_id),
        }

    def remake_input_context(self, project_id):
        found = next(
            (
                v
                for v in self._character_remakes.values()
                if v["dto"].source_type == "project" and v["dto"].target_id == project_id
            ),
            None,
        )
        return deepcopy(found["snapshot"]) if found else None

    @reference_transaction
    def create_character_remake(self, command):
        digest = remake_request_hash(command)
        existing = self._character_remakes.get(command.idempotency_key)
        if existing:
            if existing["requestHash"] != digest:
                raise StudioIdempotencyInputConflictError(
                    "idempotency key already belongs to different input"
                )
            return existing["dto"]
        canon = self.get_canon_profile(command.canon_profile_id)
        snapshot = self.character_remake_source(command.source_type, command.source_id)
        option = next(
            (v for v in self._cat_options.values() if v["canon_profile_id"] == canon.id), None
        )
        preview = remake_preview(
            command,
            snapshot,
            canon_hash=canon.profile_hash,
            label=option["label"] if option else "历史参考设定",
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("重制来源或目标已变化，请重新预览。")
        values = {**snapshot["input"], "title": preview.title}
        if command.source_type == "series":
            target = self.create_story_series(
                SeriesCreateCommand(**values), canon_profile_id=canon.id
            )
            self._series_source_beats[target.id] = [
                b.model_copy(update={"id": uuid.uuid4(), "series_id": target.id})
                for b in self._series_source_beats[command.source_id]
            ]
        else:
            target = self.create_project(ProjectCreate(**values), canon_profile_id=canon.id)
        dto = CharacterRemakeDto(
            id=uuid.uuid4(),
            sourceType=command.source_type,
            sourceId=command.source_id,
            targetId=target.id,
            canonProfileId=canon.id,
            createdAt=datetime.now(UTC),
        )
        self._character_remakes[command.idempotency_key] = {
            "dto": dto,
            "snapshot": deepcopy(snapshot),
            "requestHash": digest,
        }
        return dto
