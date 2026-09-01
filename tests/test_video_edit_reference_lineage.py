from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    SqlAlchemyAigcCanvasRepository,
)
from cat_video_generator.infrastructure.db.models import (
    Asset,
    CanvasGraphEdge,
    CanvasGraphNode,
)


class _ReferenceEdgeSession:
    def __init__(self, current_edges: list[Any]) -> None:
        self.current_edges = current_edges
        self.added: list[Any] = []
        self.deleted: list[Any] = []

    def scalars(self, _statement: Any) -> list[Any]:
        return self.current_edges

    def add(self, value: Any) -> None:
        self.added.append(value)

    def delete(self, value: Any) -> None:
        self.deleted.append(value)


def test_video_edit_reference_update_reconciles_only_compatible_canvas_edges(
    monkeypatch: Any,
) -> None:
    project_id = uuid.uuid4()
    target_node = SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=project_id,
        node_type="VideoEditNode",
    )
    kept_source = SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=project_id,
        node_type="ReferenceAssetNode",
    )
    added_source = SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=project_id,
        node_type="ImageAssetNode",
    )
    removed_source_id = uuid.uuid4()
    kept_edge = SimpleNamespace(source_node_id=kept_source.id)
    removed_edge = SimpleNamespace(source_node_id=removed_source_id)
    session = _ReferenceEdgeSession([kept_edge, removed_edge])

    kept_asset_id = uuid.uuid4()
    added_asset_id = uuid.uuid4()
    external_asset_id = uuid.uuid4()
    records = {
        (Asset, kept_asset_id): SimpleNamespace(canvas_node_id=kept_source.id),
        (Asset, added_asset_id): SimpleNamespace(canvas_node_id=added_source.id),
        (Asset, external_asset_id): SimpleNamespace(canvas_node_id=None),
        (CanvasGraphNode, kept_source.id): kept_source,
        (CanvasGraphNode, added_source.id): added_source,
    }
    events: list[tuple[str, dict[str, Any]]] = []
    repository = SqlAlchemyAigcCanvasRepository(None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        repository,
        "_required",
        lambda _session, model, object_id, **_kwargs: records[(model, object_id)],
    )
    monkeypatch.setattr(
        repository,
        "_record_event",
        lambda _session, _project_id, event_type, data: events.append((event_type, data)),
    )

    repository._sync_video_edit_reference_edges(
        session,  # type: ignore[arg-type]
        node=target_node,
        reference_asset_ids=[kept_asset_id, added_asset_id, external_asset_id],
    )

    assert session.deleted == [removed_edge]
    created_edges = [value for value in session.added if isinstance(value, CanvasGraphEdge)]
    assert len(created_edges) == 1
    assert created_edges[0].source_node_id == added_source.id
    assert created_edges[0].target_node_id == target_node.id
    assert created_edges[0].source_port == "media_reference[]"
    assert created_edges[0].target_port == "media_reference[]"
    assert events == [
        (
            "video_edit_reference_edges_synced",
            {
                "nodeId": str(target_node.id),
                "sourceNodeIds": [str(kept_source.id), str(added_source.id)],
            },
        )
    ]
