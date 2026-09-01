from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    host: str = "127.0.0.1"
    port: int = 8877

    def __post_init__(self) -> None:
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise ValueError("CatFlow host must be a numeric loopback address") from exc
        if not address.is_loopback:
            raise ValueError("CatFlow host must be loopback-only")
        if not 1024 <= self.port <= 65535:
            raise ValueError("CatFlow port must be between 1024 and 65535")

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls(port=int(os.environ.get("CATFLOW_PORT", "8877")))

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def allowed_origins(self) -> tuple[str, str]:
        return (
            f"http://127.0.0.1:{self.port}",
            f"http://localhost:{self.port}",
        )


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolve every CatFlow-owned directory against one repository boundary."""

    project_root: Path
    media_root: Path
    work_root: Path
    canon_root: Path
    log_root: Path
    backup_root: Path

    @classmethod
    def from_env(cls, project_root: Path) -> RuntimePaths:
        root = project_root.resolve()
        return cls(
            project_root=root,
            media_root=_repository_path(root, "CATFLOW_MEDIA_ROOT", "var/media"),
            work_root=_repository_path(root, "CATFLOW_WORK_ROOT", "var/work"),
            canon_root=_repository_path(root, "CATFLOW_CANON_ROOT", "assets/canon/v4"),
            log_root=_repository_path(root, "CATFLOW_LOG_ROOT", "var/logs"),
            backup_root=_repository_path(root, "CATFLOW_BACKUP_ROOT", "var/backups"),
        )


def _repository_path(project_root: Path, environment_name: str, default: str) -> Path:
    configured = Path(os.environ.get(environment_name, default))
    if configured.is_absolute():
        raise ValueError(f"{environment_name} must be a relative repository path")
    resolved = (project_root / configured).resolve()
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"{environment_name} must remain inside the repository")
    return resolved
