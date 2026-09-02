from __future__ import annotations

import pytest

from catflow.infrastructure.object_storage import ObjectStorageSettings

OBJECT_ENVIRONMENT_NAMES = (
    "CATFLOW_OBJECT_STORAGE_BACKEND",
    "CATFLOW_OBJECT_STORAGE_ENDPOINT",
    "CATFLOW_OBJECT_STORAGE_PUBLIC_ENDPOINT",
    "CATFLOW_OBJECT_STORAGE_REGION",
    "CATFLOW_OBJECT_STORAGE_BUCKET",
    "CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID",
    "CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    "CATFLOW_OBJECT_STORAGE_ADDRESSING_STYLE",
    "CATFLOW_OBJECT_STORAGE_PREFIX",
    "CATFLOW_OBJECT_STORAGE_PRESIGN_TTL_SECONDS",
    "CATFLOW_OBJECT_STORAGE_RETENTION_DAYS",
    "AccessKeyId",
    "SecretAccessKey",
)


def _clear_object_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OBJECT_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


def _configure_tos(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_object_environment(monkeypatch)
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
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ADDRESSING_STYLE", "virtual")


def test_tos_s3_settings_build_the_external_virtual_host_without_exposing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_tos(monkeypatch)

    settings = ObjectStorageSettings.from_env()

    assert settings.configured is True
    assert settings.endpoint_host == "tos-s3-cn-beijing.volces.com"
    assert settings.public_object_host == "test-vedio-ylq.tos-s3-cn-beijing.volces.com"
    assert settings.presign_ttl_seconds == 7200
    assert settings.retention_days == 7
    assert "test-access" not in repr(settings)
    assert "test-secret" not in repr(settings)


def test_legacy_credentials_are_a_deprecated_fallback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_tos(monkeypatch)
    monkeypatch.delenv("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID")
    monkeypatch.delenv("CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY")
    monkeypatch.setenv("AccessKeyId", "legacy-access")
    monkeypatch.setenv("SecretAccessKey", "legacy-secret")

    with pytest.warns(DeprecationWarning, match="CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID"):
        settings = ObjectStorageSettings.from_env()

    assert settings.configured is True
    assert settings.access_key_id == "legacy-access"
    assert settings.secret_access_key == "legacy-secret"


def test_tos_non_secret_defaults_allow_one_release_legacy_credential_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_object_environment(monkeypatch)
    monkeypatch.setenv("AccessKeyId", "legacy-access")
    monkeypatch.setenv("SecretAccessKey", "legacy-secret")

    with pytest.warns(DeprecationWarning):
        settings = ObjectStorageSettings.from_env()

    assert settings.configured is True
    assert settings.backend == "s3"
    assert settings.endpoint_url == "https://tos-s3-cn-beijing.volces.com"
    assert settings.public_endpoint_url == "https://tos-s3-cn-beijing.volces.com"
    assert settings.region == "cn-beijing"
    assert settings.bucket == "test-vedio-ylq"
    assert settings.addressing_style == "virtual"


def test_tos_s3_endpoint_rejects_path_style(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_tos(monkeypatch)
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ADDRESSING_STYLE", "path")

    with pytest.raises(ValueError, match="VirtualHostStyle"):
        ObjectStorageSettings.from_env()


@pytest.mark.parametrize(
    "public_endpoint",
    (
        "http://media.example.com",
        "https://127.0.0.1:9000",
        "https://192.168.1.20",
        "https://tos-s3-cn-beijing.ivolces.com",
    ),
)
def test_public_endpoint_rejects_non_public_or_non_https_hosts(
    monkeypatch: pytest.MonkeyPatch,
    public_endpoint: str,
) -> None:
    _configure_tos(monkeypatch)
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_PUBLIC_ENDPOINT", public_endpoint)

    with pytest.raises(ValueError, match="public|HTTPS|VPC"):
        ObjectStorageSettings.from_env()


def test_minio_may_upload_over_loopback_http_but_still_publishes_public_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_object_environment(monkeypatch)
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    monkeypatch.setenv(
        "CATFLOW_OBJECT_STORAGE_PUBLIC_ENDPOINT", "https://media.example.com"
    )
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_REGION", "us-east-1")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_BUCKET", "catflow-temp")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID", "minio-access")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY", "minio-secret")
    monkeypatch.setenv("CATFLOW_OBJECT_STORAGE_ADDRESSING_STYLE", "path")

    settings = ObjectStorageSettings.from_env()

    assert settings.configured is True
    assert settings.endpoint_url == "http://127.0.0.1:9000"
    assert settings.public_object_host == "media.example.com"
