from __future__ import annotations

import pytest

from catflow import config as runtime_config
from catflow.application.provider_config import ProviderRuntime
from catflow.config import RuntimeConfig


def test_runtime_config_defaults_to_port_8877_and_loopback_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATFLOW_PORT", raising=False)

    config = RuntimeConfig.from_env()

    assert config.port == 8877
    assert config.base_url == "http://127.0.0.1:8877"
    assert config.allowed_origins == (
        "http://127.0.0.1:8877",
        "http://localhost:8877",
    )


def test_runtime_config_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError, match="loopback"):
        RuntimeConfig(host="0.0.0.0", port=8877)


def test_runtime_paths_resolve_all_mutable_roots_below_the_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    assert hasattr(runtime_config, "RuntimePaths")
    monkeypatch.setenv("CATFLOW_MEDIA_ROOT", "var/media-custom")
    monkeypatch.setenv("CATFLOW_WORK_ROOT", "var/work-custom")
    monkeypatch.setenv("CATFLOW_CANON_ROOT", "assets/canon/v4")

    paths = runtime_config.RuntimePaths.from_env(tmp_path)

    assert paths.project_root == tmp_path.resolve()
    assert paths.media_root == (tmp_path / "var/media-custom").resolve()
    assert paths.work_root == (tmp_path / "var/work-custom").resolve()
    assert paths.canon_root == (tmp_path / "assets/canon/v4").resolve()


@pytest.mark.parametrize("value", ["C:/catflow-media", "../outside", "var/../../outside"])
def test_runtime_paths_reject_absolute_and_repository_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path, value: str,
) -> None:
    assert hasattr(runtime_config, "RuntimePaths")
    monkeypatch.setenv("CATFLOW_MEDIA_ROOT", value)

    with pytest.raises(ValueError, match="relative|repository"):
        runtime_config.RuntimePaths.from_env(tmp_path)


def test_provider_runtime_reads_ark_models_without_ever_owning_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATFLOW_PAID_CALLS_ENABLED", "true")
    monkeypatch.setenv("ARK_PLANNING_MODEL", "planning-model")
    monkeypatch.setenv("ARK_IMAGE_MODEL", "image-model")
    monkeypatch.setenv("ARK_VIDEO_MODEL", "video-model")

    runtime = ProviderRuntime.from_env(segment_reference_publishing_ready=False)

    assert runtime.provider == "ark"
    assert runtime.paid_calls_enabled is True
    assert runtime.planning_model == "planning-model"
    assert runtime.image_model == "image-model"
    assert runtime.video_model == "video-model"
    assert not hasattr(runtime, "api_key")

def test_ark_runtime_blocks_segment_repair_without_an_https_reference_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATFLOW_PAID_CALLS_ENABLED", "true")
    monkeypatch.delenv("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AccessKeyId", raising=False)
    monkeypatch.delenv("SecretAccessKey", raising=False)

    runtime = ProviderRuntime.from_env()

    assert runtime.segment_repair_supported is False
    assert runtime.segment_repair_block_reason == (
        "Ark 片段修复需要先把本地上下文视频安全发布为 Provider 可读取的 HTTPS URL；"
        "当前未配置受管发布器。"
    )


def test_ark_runtime_enables_segment_repair_when_s3_publication_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv(
        "CATFLOW_OBJECT_STORAGE_ENDPOINT", "https://tos-s3-cn-beijing.volces.com"
    )
    monkeypatch.setenv(
        "CATFLOW_OBJECT_STORAGE_PUBLIC_ENDPOINT",
        "https://tos-s3-cn-beijing.volces.com",
    )
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_REGION", "cn-beijing")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_BUCKET", "test-vedio-ylq")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ADDRESSING_STYLE", "virtual")

    runtime = ProviderRuntime.from_env()

    assert runtime.segment_repair_supported is True
    assert runtime.segment_repair_block_reason is None
