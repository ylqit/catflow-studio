from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
import socket
import threading
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

DEFAULT_TOS_S3_ENDPOINT = "https://tos-s3-cn-beijing.volces.com"
DEFAULT_TOS_REGION = "cn-beijing"
DEFAULT_TOS_BUCKET = "test-vedio-ylq"
LEGACY_ACCESS_KEY_ENV = "AccessKeyId"
LEGACY_SECRET_KEY_ENV = "SecretAccessKey"


class ObjectPublisherError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_key: str
    sha256: str
    byte_size: int
    etag: str


@dataclass(frozen=True, slots=True)
class PublisherCheckResult:
    ready: bool
    public_host: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class ObjectPublisherStatus:
    configured: bool
    ready: bool
    backend: str
    endpoint_host: str
    public_host: str
    bucket: str
    region: str
    addressing_style: str
    presign_ttl_seconds: int
    retention_days: int
    error: dict[str, str] | None = None

    def as_document(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "ready": self.ready,
            "backend": self.backend,
            "endpointHost": self.endpoint_host,
            "publicHost": self.public_host,
            "bucket": self.bucket,
            "region": self.region,
            "addressingStyle": self.addressing_style,
            "presignTtlSeconds": self.presign_ttl_seconds,
            "retentionDays": self.retention_days,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ObjectStorageSettings:
    backend: str
    endpoint_url: str
    public_endpoint_url: str
    region: str
    bucket: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    addressing_style: str
    prefix: str
    presign_ttl_seconds: int
    retention_days: int

    def __post_init__(self) -> None:
        if self.backend not in {"", "s3"}:
            raise ValueError(f"unsupported object storage backend: {self.backend}")
        if self.addressing_style not in {"virtual", "path"}:
            raise ValueError("object storage addressing style must be virtual or path")
        if not 60 <= self.presign_ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("object storage presign TTL must be between 60 seconds and 30 days")
        if not 1 <= self.retention_days <= 30:
            raise ValueError("object storage retention must be between 1 and 30 days")
        if self.prefix.startswith("/") or ".." in self.prefix.split("/"):
            raise ValueError("object storage prefix must be a relative object-key prefix")

        endpoint = _parse_endpoint(self.endpoint_url, public=False) if self.endpoint_url else None
        public = (
            _parse_endpoint(self.public_endpoint_url, public=True)
            if self.public_endpoint_url
            else None
        )
        if (
            endpoint
            and _is_tos_s3_host(endpoint.hostname or "")
            and self.addressing_style != "virtual"
        ):
            raise ValueError("TOS S3 requires VirtualHostStyle addressing")
        if (
            public
            and _is_tos_s3_host(public.hostname or "")
            and self.addressing_style != "virtual"
        ):
            raise ValueError("TOS S3 requires VirtualHostStyle addressing")

    @classmethod
    def from_env(cls) -> ObjectStorageSettings:
        access_key_id = os.environ.get("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID", "").strip()
        secret_access_key = os.environ.get(
            "CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY", ""
        ).strip()
        legacy_access = os.environ.get(LEGACY_ACCESS_KEY_ENV, "").strip()
        legacy_secret = os.environ.get(LEGACY_SECRET_KEY_ENV, "").strip()
        if not access_key_id and legacy_access:
            warnings.warn(
                "AccessKeyId is deprecated; use CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID",
                DeprecationWarning,
                stacklevel=2,
            )
            access_key_id = legacy_access
        if not secret_access_key and legacy_secret:
            if not legacy_access:
                warnings.warn(
                    "SecretAccessKey is deprecated; use "
                    "CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY",
                    DeprecationWarning,
                    stacklevel=2,
                )
            secret_access_key = legacy_secret

        endpoint = os.environ.get(
            "CATFLOW_OBJECT_STORAGE_ENDPOINT", DEFAULT_TOS_S3_ENDPOINT
        ).strip().rstrip("/")
        public_endpoint = os.environ.get(
            "CATFLOW_OBJECT_STORAGE_PUBLIC_ENDPOINT", endpoint
        ).strip().rstrip("/")
        return cls(
            backend=os.environ.get("CATFLOW_OBJECT_STORAGE_BACKEND", "s3").strip().lower(),
            endpoint_url=endpoint,
            public_endpoint_url=public_endpoint,
            region=os.environ.get("CATFLOW_OBJECT_STORAGE_REGION", DEFAULT_TOS_REGION).strip(),
            bucket=os.environ.get("CATFLOW_OBJECT_STORAGE_BUCKET", DEFAULT_TOS_BUCKET).strip(),
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            addressing_style=os.environ.get(
                "CATFLOW_OBJECT_STORAGE_ADDRESSING_STYLE", "virtual"
            ).strip().lower(),
            prefix=os.environ.get(
                "CATFLOW_OBJECT_STORAGE_PREFIX", "catflow/segment-references"
            ).strip().strip("/"),
            presign_ttl_seconds=int(
                os.environ.get("CATFLOW_OBJECT_STORAGE_PRESIGN_TTL_SECONDS", "7200")
            ),
            retention_days=int(
                os.environ.get("CATFLOW_OBJECT_STORAGE_RETENTION_DAYS", "7")
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.backend == "s3"
            and self.endpoint_url
            and self.public_endpoint_url
            and self.region
            and self.bucket
            and self.access_key_id
            and self.secret_access_key
        )

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self.endpoint_url).hostname or ""

    @property
    def public_endpoint_host(self) -> str:
        return urlsplit(self.public_endpoint_url).hostname or ""

    @property
    def public_object_host(self) -> str:
        if self.addressing_style == "virtual" and self.bucket:
            return f"{self.bucket}.{self.public_endpoint_host}"
        return self.public_endpoint_host


class S3ObjectStore:
    """Own the external S3-compatible upload, verification, signing and deletion boundary."""

    def __init__(
        self,
        settings: ObjectStorageSettings,
        *,
        upload_client: Any | None = None,
        public_client: Any | None = None,
        http_client: httpx.Client | None = None,
        host_resolver: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        if not settings.configured:
            raise ObjectPublisherError(
                "object_storage_not_configured",
                "S3-compatible object storage is not fully configured",
            )
        self.settings = settings
        self._upload_client = upload_client or _new_s3_client(settings, settings.endpoint_url)
        self._public_client = public_client or _new_s3_client(
            settings, settings.public_endpoint_url
        )
        self._http_client = http_client or httpx.Client(
            timeout=httpx.Timeout(30), follow_redirects=False
        )
        self._host_resolver = host_resolver or _resolve_host

    def upload_verified(
        self,
        path: Path,
        *,
        object_key: str,
        expected_sha256: str,
    ) -> StoredObject:
        _validate_object_key(object_key)
        if not path.is_file():
            raise ObjectPublisherError("source_missing", "segment reference source is missing")
        byte_size = path.stat().st_size
        if _sha256(path) != expected_sha256:
            raise ObjectPublisherError(
                "source_sha256_mismatch", "segment reference source SHA256 does not match"
            )
        try:
            self._upload_client.upload_file(
                str(path),
                self.settings.bucket,
                object_key,
                ExtraArgs={
                    "ContentType": "video/mp4",
                    "Metadata": {"sha256": expected_sha256},
                },
            )
        except Exception as exc:
            raise _classify_s3_error("upload", exc) from exc
        return self.verify_object(
            object_key,
            expected_sha256=expected_sha256,
            expected_byte_size=byte_size,
        )

    def verify_object(
        self,
        object_key: str,
        *,
        expected_sha256: str,
        expected_byte_size: int,
    ) -> StoredObject:
        _validate_object_key(object_key)
        try:
            head = self._upload_client.head_object(
                Bucket=self.settings.bucket,
                Key=object_key,
            )
        except Exception as exc:
            raise _classify_s3_error("head", exc) from exc
        metadata = head.get("Metadata") if isinstance(head, dict) else None
        remote_sha256 = metadata.get("sha256") if isinstance(metadata, dict) else None
        remote_size = head.get("ContentLength") if isinstance(head, dict) else None
        if remote_sha256 != expected_sha256:
            raise ObjectPublisherError(
                "remote_sha256_mismatch", "published object SHA256 metadata does not match"
            )
        if remote_size != expected_byte_size:
            raise ObjectPublisherError(
                "remote_size_mismatch", "published object byte size does not match"
            )
        etag = str(head.get("ETag", "")).strip('"')
        return StoredObject(
            object_key=object_key,
            sha256=expected_sha256,
            byte_size=expected_byte_size,
            etag=etag,
        )

    def presign_get(self, object_key: str) -> str:
        _validate_object_key(object_key)
        self._require_public_dns()
        try:
            url = str(
                self._public_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.settings.bucket, "Key": object_key},
                    ExpiresIn=self.settings.presign_ttl_seconds,
                )
            )
        except Exception as exc:
            raise _classify_s3_error("presign", exc) from exc
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.settings.public_object_host
            or parsed.username
            or parsed.password
        ):
            raise ObjectPublisherError(
                "invalid_presigned_host",
                "presigned object URL does not use the configured public object host",
            )
        return url

    def delete(self, object_key: str) -> None:
        _validate_object_key(object_key)
        try:
            self._upload_client.delete_object(Bucket=self.settings.bucket, Key=object_key)
        except Exception as exc:
            raise _classify_s3_error("delete", exc) from exc

    def head_bucket(self) -> None:
        self._require_public_dns()
        try:
            self._upload_client.head_bucket(Bucket=self.settings.bucket)
        except Exception as exc:
            raise _classify_s3_error("bucket", exc) from exc

    def check_roundtrip(self) -> PublisherCheckResult:
        payload = secrets.token_bytes(1024)
        digest = hashlib.sha256(payload).hexdigest()
        namespace = self.settings.prefix.split("/", 1)[0] or "catflow"
        object_key = f"{namespace}/publisher-checks/{secrets.token_hex(16)}.bin"
        deleted = False
        try:
            try:
                self._upload_client.put_object(
                    Bucket=self.settings.bucket,
                    Key=object_key,
                    Body=payload,
                    ContentType="application/octet-stream",
                    Metadata={"sha256": digest},
                )
            except Exception as exc:
                raise _classify_s3_error("check_upload", exc) from exc
            self.verify_object(
                object_key,
                expected_sha256=digest,
                expected_byte_size=len(payload),
            )
            signed_url = self.presign_get(object_key)
            try:
                response = self._http_client.get(signed_url)
            except Exception as exc:
                raise ObjectPublisherError(
                    "object_signed_fetch_failed", "signed object fetch failed"
                ) from exc
            if response.status_code != 200 or response.content != payload:
                raise ObjectPublisherError(
                    "object_signed_fetch_mismatch",
                    "signed object fetch did not return the uploaded bytes",
                )
        finally:
            try:
                self.delete(object_key)
                deleted = True
            except ObjectPublisherError:
                if deleted:
                    raise
        if not deleted:
            raise ObjectPublisherError(
                "publisher_check_cleanup_failed", "publisher check object could not be deleted"
            )
        return PublisherCheckResult(
            ready=True,
            public_host=self.settings.public_object_host,
            deleted=True,
        )

    def _require_public_dns(self) -> None:
        try:
            addresses = self._host_resolver(self.settings.public_object_host)
        except Exception as exc:
            raise ObjectPublisherError(
                "public_host_resolution_failed", "public object host could not be resolved"
            ) from exc
        if not addresses:
            raise ObjectPublisherError(
                "public_host_resolution_failed", "public object host resolved no addresses"
            )
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise ObjectPublisherError(
                    "invalid_public_address", "public object host returned an invalid IP address"
                ) from exc
            if not address.is_global:
                raise ObjectPublisherError(
                    "private_publication_address",
                    "public object host must resolve only to public IP addresses",
                )


class ObjectPublisherRuntime:
    """Own process-level publisher readiness without exposing credentials or signed URLs."""

    def __init__(
        self,
        settings: ObjectStorageSettings,
        store: S3ObjectStore | None = None,
        *,
        check_on_start: bool = True,
    ) -> None:
        self.settings = settings
        self.store = store
        self._ready = False
        self._error: dict[str, str] | None = None
        self._lock = threading.Lock()
        if not settings.configured:
            self._error = {
                "code": "object_storage_not_configured",
                "message": "S3-compatible object storage is not fully configured",
            }
            return
        if self.store is None:
            self.store = S3ObjectStore(settings)
        if check_on_start:
            self._check_bucket()

    @classmethod
    def disabled(cls) -> ObjectPublisherRuntime:
        return cls(
            ObjectStorageSettings(
                backend="",
                endpoint_url="",
                public_endpoint_url="",
                region="",
                bucket="",
                access_key_id="",
                secret_access_key="",
                addressing_style="virtual",
                prefix="catflow/segment-references",
                presign_ttl_seconds=7200,
                retention_days=7,
            ),
            check_on_start=False,
        )

    @classmethod
    def from_env(cls, *, check_on_start: bool = True) -> ObjectPublisherRuntime:
        return cls(ObjectStorageSettings.from_env(), check_on_start=check_on_start)

    @property
    def status(self) -> ObjectPublisherStatus:
        return ObjectPublisherStatus(
            configured=self.settings.configured,
            ready=self._ready,
            backend=self.settings.backend or "s3",
            endpoint_host=self.settings.endpoint_host,
            public_host=self.settings.public_object_host,
            bucket=self.settings.bucket,
            region=self.settings.region,
            addressing_style=self.settings.addressing_style,
            presign_ttl_seconds=self.settings.presign_ttl_seconds,
            retention_days=self.settings.retention_days,
            error=self._error,
        )

    def check_roundtrip(self) -> ObjectPublisherStatus:
        if self.store is None:
            raise ObjectPublisherError(
                "object_storage_not_configured",
                "S3-compatible object storage is not fully configured",
            )
        with self._lock:
            try:
                self.store.check_roundtrip()
            except ObjectPublisherError as exc:
                self._ready = False
                self._error = {"code": exc.code, "message": exc.message}
                raise
            self._ready = True
            self._error = None
            return self.status

    def _check_bucket(self) -> None:
        if self.store is None:
            return
        try:
            self.store.head_bucket()
        except ObjectPublisherError as exc:
            self._error = {"code": exc.code, "message": exc.message}
            self._ready = False
            return
        self._ready = True
        self._error = None


def _parse_endpoint(value: str, *, public: bool):
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("object storage endpoint must be an absolute URL without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("object storage endpoint cannot include a path, query, or fragment")
    if public:
        if parsed.scheme != "https":
            raise ValueError("object storage public endpoint must use HTTPS")
        _validate_public_host(parsed.hostname)
    elif parsed.scheme != "https" and not (
        parsed.scheme == "http" and _is_loopback_host(parsed.hostname)
    ):
        raise ValueError("object storage upload endpoint must use HTTPS or loopback HTTP")
    return parsed


def _validate_public_host(host: str) -> None:
    lowered = host.rstrip(".").lower()
    if lowered.endswith(".ivolces.com") or lowered == "ivolces.com":
        raise ValueError("object storage public endpoint cannot use a VPC host")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        if lowered == "localhost":
            raise ValueError(
                "object storage public endpoint must use a public host"
            ) from None
        return
    if not address.is_global:
        raise ValueError("object storage public endpoint must use a public IP address")


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_tos_s3_host(host: str) -> bool:
    lowered = host.rstrip(".").lower()
    return ".tos-s3-" in f".{lowered}" and lowered.endswith((".volces.com", ".ivolces.com"))


def _new_s3_client(settings: ObjectStorageSettings, endpoint_url: str):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise ObjectPublisherError(
            "s3_dependency_missing", "boto3 is required for S3-compatible object publication"
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.addressing_style},
        ),
    )


def _resolve_host(host: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                result[4][0]
                for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
        )
    )


def _validate_object_key(value: str) -> None:
    if not value or value.startswith("/") or ".." in value.split("/"):
        raise ObjectPublisherError("invalid_object_key", "object key must be a relative exact key")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_sdk_message(exc: Exception) -> str:
    name = type(exc).__name__
    return f"S3-compatible object operation failed ({name})"


def _classify_s3_error(operation: str, exc: Exception) -> ObjectPublisherError:
    if isinstance(
        exc,
        (
            EndpointConnectionError,
            ConnectTimeoutError,
            ReadTimeoutError,
            ConnectionClosedError,
        ),
    ):
        return ObjectPublisherError(
            "object_storage_network_error",
            "S3-compatible object storage could not be reached",
        )
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", "")).lower()
        if code in {
            "accessdenied",
            "allaccessdisabled",
            "forbidden",
            "invalidaccesskeyid",
        }:
            return ObjectPublisherError(
                "object_storage_permission_denied",
                "S3-compatible object storage denied the configured identity",
            )
        if code in {
            "authorizationheadermalformed",
            "invalidsignature",
            "requesttimetoolskewed",
            "signaturedoesnotmatch",
        }:
            return ObjectPublisherError(
                "object_storage_signature_invalid",
                "S3-compatible object storage rejected the request signature",
            )
        if operation == "head" and code in {
            "404",
            "nosuchkey",
            "notfound",
        }:
            return ObjectPublisherError("object_not_found", "published object is absent")
    return ObjectPublisherError(f"object_{operation}_failed", _safe_sdk_message(exc))
