from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from catflow.infrastructure.object_storage import (
    ObjectPublisherError,
    ObjectStorageSettings,
    S3ObjectStore,
)


class RecordingS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str]] = {}
        self.deleted: list[tuple[str, str]] = []
        self.presigned_url: str | None = None
        self.put_error: Exception | None = None
        self.head_error: Exception | None = None
        self.presign_error: Exception | None = None

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, object],
    ) -> None:
        payload = Path(filename).read_bytes()
        metadata = dict(ExtraArgs["Metadata"])  # type: ignore[arg-type]
        self.objects[(bucket, key)] = (payload, metadata, '"etag-value"')

    def put_object(self, **request: object) -> dict[str, str]:
        if self.put_error is not None:
            raise self.put_error
        body = bytes(request["Body"])  # type: ignore[arg-type]
        bucket = str(request["Bucket"])
        key = str(request["Key"])
        metadata = dict(request["Metadata"])  # type: ignore[arg-type]
        self.objects[(bucket, key)] = (body, metadata, '"check-etag"')
        return {"ETag": '"check-etag"'}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        if self.head_error is not None:
            raise self.head_error
        payload, metadata, etag = self.objects[(Bucket, Key)]
        return {"ContentLength": len(payload), "Metadata": metadata, "ETag": etag}

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, str],
        ExpiresIn: int,
    ) -> str:
        if self.presign_error is not None:
            raise self.presign_error
        assert operation == "get_object"
        assert Params["Bucket"] == "test-vedio-ylq"
        assert ExpiresIn == 7200
        return self.presigned_url or (
            "https://test-vedio-ylq.tos-s3-cn-beijing.volces.com/"
            f"{Params['Key']}?secret=yes"
        )

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deleted.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)

    def head_bucket(self, *, Bucket: str) -> dict[str, object]:
        assert Bucket == "test-vedio-ylq"
        return {}


def _settings() -> ObjectStorageSettings:
    return ObjectStorageSettings(
        backend="s3",
        endpoint_url="https://tos-s3-cn-beijing.volces.com",
        public_endpoint_url="https://tos-s3-cn-beijing.volces.com",
        region="cn-beijing",
        bucket="test-vedio-ylq",
        access_key_id="access",
        secret_access_key="secret",
        addressing_style="virtual",
        prefix="catflow/segment-references",
        presign_ttl_seconds=7200,
        retention_days=7,
    )


def test_upload_verifies_the_remote_sha256_and_returns_non_secret_metadata(tmp_path) -> None:
    source = tmp_path / "context.mp4"
    source.write_bytes(b"catflow-context-video")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    client = RecordingS3Client()
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("8.8.8.8",),
    )

    stored = store.upload_verified(
        source,
        object_key=f"catflow/segment-references/project/job/{digest}.mp4",
        expected_sha256=digest,
    )

    assert stored.byte_size == len(b"catflow-context-video")
    assert stored.etag == "etag-value"
    assert stored.sha256 == digest
    assert "secret" not in repr(stored)


def test_upload_rejects_remote_content_metadata_that_does_not_match(tmp_path) -> None:
    source = tmp_path / "context.mp4"
    source.write_bytes(b"catflow-context-video")
    client = RecordingS3Client()
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("8.8.8.8",),
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    client.upload_file(
        str(source),
        "test-vedio-ylq",
        "existing",
        {"Metadata": {"sha256": "0" * 64}},
    )

    with pytest.raises(ObjectPublisherError, match="SHA256"):
        store.verify_object(
            "existing",
            expected_sha256=digest,
            expected_byte_size=source.stat().st_size,
        )


def test_presigned_url_must_use_the_configured_public_object_host() -> None:
    client = RecordingS3Client()
    client.presigned_url = "https://attacker.example/object.mp4?secret=yes"
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("8.8.8.8",),
    )

    with pytest.raises(ObjectPublisherError, match="host") as captured:
        store.presign_get("catflow/segment-references/project/job/context.mp4")

    assert "secret=yes" not in str(captured.value)


def test_dns_resolution_to_private_space_blocks_publication() -> None:
    client = RecordingS3Client()
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("100.64.1.10",),
    )

    with pytest.raises(ObjectPublisherError, match="public IP"):
        store.presign_get("catflow/segment-references/project/job/context.mp4")


def test_roundtrip_check_uploads_fetches_and_deletes_without_returning_the_signed_url() -> None:
    client = RecordingS3Client()

    def fetch(request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        payload = client.objects[("test-vedio-ylq", key)][0]
        return httpx.Response(200, content=payload)

    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        http_client=httpx.Client(transport=httpx.MockTransport(fetch)),
        host_resolver=lambda _host: ("8.8.8.8",),
    )

    result = store.check_roundtrip()

    assert result.ready is True
    assert result.public_host == "test-vedio-ylq.tos-s3-cn-beijing.volces.com"
    assert result.deleted is True
    assert not hasattr(result, "url")
    assert client.deleted and client.deleted[0][1].startswith("catflow/publisher-checks/")


def test_roundtrip_upload_error_is_classified_without_leaking_sdk_details() -> None:
    client = RecordingS3Client()
    client.put_error = RuntimeError(
        "AccessDenied https://bucket/object?X-Amz-Credential=access&X-Amz-Signature=secret"
    )
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("8.8.8.8",),
    )

    with pytest.raises(ObjectPublisherError) as captured:
        store.check_roundtrip()

    assert captured.value.code == "object_check_upload_failed"
    assert "Credential" not in str(captured.value)
    assert "Signature" not in str(captured.value)


def test_permission_error_is_not_misclassified_as_an_absent_object() -> None:
    client = RecordingS3Client()
    client.head_error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "signed secret details"}},
        "HeadObject",
    )
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("8.8.8.8",),
    )

    with pytest.raises(ObjectPublisherError) as captured:
        store.verify_object("existing", expected_sha256="0" * 64, expected_byte_size=1)

    assert captured.value.code == "object_storage_permission_denied"
    assert "secret" not in str(captured.value)


def test_network_failure_has_a_stable_non_secret_error_code() -> None:
    client = RecordingS3Client()
    client.put_error = EndpointConnectionError(endpoint_url="https://secret.example")
    store = S3ObjectStore(
        _settings(),
        upload_client=client,
        public_client=client,
        host_resolver=lambda _host: ("8.8.8.8",),
    )

    with pytest.raises(ObjectPublisherError) as captured:
        store.check_roundtrip()

    assert captured.value.code == "object_storage_network_error"
    assert "secret.example" not in str(captured.value)
