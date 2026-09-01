from __future__ import annotations

import json
from pathlib import Path

from catflow.application.service import StudioService
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output = project_root / "packages" / "contracts" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    application = create_app(
        StudioService(MemoryStudioRepository()),
        settings=AppSettings(csrf_token="openapi-generation-only"),
    )
    output.write_text(
        json.dumps(application.openapi(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
