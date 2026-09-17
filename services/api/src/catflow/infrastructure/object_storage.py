"""TOS S3 兼容对象存储 —— CatFlow 的"外部隐私桥"。

为什么需要对象存储?
- Ark 视频修复接口拒绝 data:video/mp4;base64,... 形式(已验证)
- 必须给 Ark 一个公网可访问的 HTTPS URL,让 Ark 去拉 MP4
- 但本机 MP4 不能直接暴露公网(loopback-only 安全模型)
- 解决:把本机 MP4 上传到私有 S3-compatible 桶(TOS),签发 2 小时 HTTPS URL,提交给 Ark

本文件构成:
1. ObjectStorageSettings —— 配置对象(从 .env 加载,严格 .env.example 注释)
2. S3ObjectStore       —— 上传 / HEAD 校验 / 预签名 / 删除
3. ObjectPublisherRuntime —— 进程级 ready 标志(供 runtime.bootstrap 端点读取)
4. check_roundtrip     —— 健康自检:上传 1KB 随机字节 → 预签名 → HTTP GET → 校验回环
5. 错误分类            —— 把 botocore SDK 异常映射成 8 类 ObjectPublisherError

安全约束(关键):
- public_endpoint 必须 必须 必须 HTTPS,且 host 解析为公网 IP(拒绝 *.ivolces.com VPC)
- object_key 必须相对且不含 ..(防越界写)
- presigned URL 必须指向配置的 public_object_host(防 host 替换攻击)
- IAM 最小权限:CatFlow 在 TOS 只申请 catflow/segment-references/* 与 catflow/publisher-checks/* 的
  PutObject / GetObject / HeadObject / DeleteObject,不申请公开读或桶管理权限
"""

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

# ─────────────────── 默认值 ───────────────────
# TOS 桶 + 区域 + 端点常量(默认指向火山引擎 TOS,但用户可改成 MinIO 等)
DEFAULT_TOS_S3_ENDPOINT = "https://tos-s3-cn-beijing.volces.com"
DEFAULT_TOS_REGION = "cn-beijing"
DEFAULT_TOS_BUCKET = "test-vedio-ylq"
# 旧版本 .env 用过 AccessKeyId / SecretAccessKey 作为变量名;新版本规范化
# 但仍兼容旧名(发 DeprecationWarning,见 ObjectStorageSettings.from_env)
LEGACY_ACCESS_KEY_ENV = "AccessKeyId"
LEGACY_SECRET_KEY_ENV = "SecretAccessKey"


class ObjectPublisherError(RuntimeError):
    """对象存储层的可操作错误,带 code 字段供前端分类提示。

    已覆盖的 code:
    - object_storage_not_configured      —— 后端启动时 .env 缺关键配置
    - source_missing / source_sha256_mismatch —— 上传源文件丢失或 SHA256 不匹配
    - remote_sha256_mismatch / remote_size_mismatch —— TOS 端内容与本机不一致(数据腐败)
    - invalid_object_key                —— object_key 含 .. 或以 / 开头
    - object_storage_network_error     —— TOS 不可达
    - object_storage_permission_denied —— 凭据无权限(AccessKeyId 错 / IAM 不足)
    - object_storage_signature_invalid  —— 签名错误(时区错位 / SecretKey 错)
    - object_not_found                  —— HEAD 对象不存在(404)
    - invalid_presigned_host             —— 预签名 URL host 不匹配(可能被劫持)
    - private_publication_address       —— public_endpoint 解析到内网 IP(VPC 泄露风险)
    - public_host_resolution_failed     —— DNS 无法解析 public endpoint
    - invalid_public_address            —— 解析结果是无效 IP
    - object_storage_not_configured     —— 启动时未配置
    - object_signed_fetch_failed / _mismatch —— check_roundtrip 时拉取失败
    - publisher_check_cleanup_failed    —— check_roundtrip 后清理失败
    - object_upload / _head / _presign / _delete / _bucket / _check_upload _failed
                                              —— 通用 SDK 错误,保留 operation 上下文
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class StoredObject:
    """一次成功上传的返回元数据。

    Attributes:
        object_key: 对象在桶内的 key(含 CATFLOW_OBJECT_STORAGE_PREFIX 前缀)
        sha256: 对象内容的 SHA256(hex)
        byte_size: 对象字节数
        etag: TOS 返回的 ETag(MD5,可能带双引号)
    """
    object_key: str
    sha256: str
    byte_size: int
    etag: str


@dataclass(frozen=True, slots=True)
class PublisherCheckResult:
    """check_roundtrip 成功后的状态摘要。

    Attributes:
        ready: 是否完全 ready(上传 + 预签名 GET + 内容字节匹配)
        public_host: TOS 公开访问 host(bucket.volces.com 形式)
        deleted: 测试对象是否清理清理成功
    """
    ready: bool
    public_host: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class ObjectPublisherStatus:
    """进程级 publisher 状态快照(用于 /api/v1/runtime/bootstrap 端点)。

    Attributes:
        configured: 后端 .env 是否完整配置(8 个字段全填)
        ready: 启动时 HEAD bucket 是否成功
        backend: 后端类型(s3 / 空字符串表示未配置)
        endpoint_host: 内部上传 endpoint 的 host
        public_host: 公开(预签名)的 host
        bucket: TOS 桶名
        region: TOS 区域(cn-beijing)
        addressing_style: virtual(子域) / path(路径) — TOS 必须 virtual
        presign_ttl_seconds: 预签名 URL 过期时间(默认 7200 秒)
        retention_days: 对象保留天数(默认 7)
        error: 最近的错误 {code, message};None 表示无错误
    """

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
        """转为 camelCase 字典(供前端 JSON 序列化)。"""
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
    """TOS 配置对象(冻结;修改需要重建)。

    Attributes:
        backend: "s3" 或 ""(空 = 未配置)
        endpoint_url: 内部上传 endpoint(可 HTTPS 或 loopback HTTP for MinIO)
        public_endpoint_url: 公开(预签名)endpoint —— Ark 拉取的来源
        region: TOS 区域
        bucket: 桶名
        access_key_id / secret_access_key: 凭据(repr=False 防泄漏)
        addressing_style: virtual(默认,TOS 强制)/ path(MinIO)
        prefix: 对象 key 前缀(catflow/segment-references)
        presign_ttl_seconds: 60~30 天
        retention_days: 1~30 天(目前只用于文档,真实 retention 需 TOS 桶生命周期规则)
    """

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
        """冻结后校验:防止后端启动时配置错误导致运行时崩溃。"""
        # 1. 后端类型(目前仅支持 s3 / 关闭)
        if self.backend not in {"", "s3"}:
            raise ValueError(f"unsupported object storage backend: {self.backend}")
        # 2. TOS 强制 virtual host addressing(否则 403 SignatureDoesNotMatch)
        if self.addressing_style not in {"virtual", "path"}:
            raise ValueError("object storage addressing style must be virtual or path")
        # 3. 预签名 TTL 范围:60 秒(太短 Ark 无法下载)~30 天(超出 S3 上限)
        if not 60 <= self.presign_ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("object storage presign TTL must be between 60 seconds and 30 days")
        # 4. retention 范围:1~30 天
        if not 1 <= self.retention_days <= 30:
            raise ValueError("object storage retention must be between 1 and 30 days")
        # 5. prefix 必须相对,不能以 / 开头或含 ..(防越界)
        if self.prefix.startswith("/") or ".." in self.prefix.split("/"):
            raise ValueError("object storage prefix must be a relative object-key prefix")

        # 6. endpoint 必须合法 URL(详见 _parse_endpoint 注释)
        endpoint = _parse_endpoint(self.endpoint_url, public=False) if self.endpoint_url else None
        public = (
            _parse_endpoint(self.public_endpoint_url, public=True)
            if self.public_endpoint_url
            else None
        )
        # 7. TOS 强制 VirtualHostStyle(path-style 会导致 bucket 名出现在 path 里,
        # TOS 期望 bucket.s3-region.volces.com 子域名形式)
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
        """从 .env 加载(每个字段独立取,缺省有合理 fallback)。"""
        access_key_id = os.environ.get("CATFLOW_OBJECT_STORAGE_ACCESS_KEY_ID", "").strip()
        secret_access_key = os.environ.get(
            "CATFLOW_OBJECT_STORAGE_SECRET_ACCESS_KEY", ""
        ).strip()
        # 兼容旧变量名 AccessKeyId / SecretAccessKey(2026-09-11 之前)
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

        # endpoint 自动 strip 末尾 /,避免拼接时出现双斜杠
        endpoint = os.environ.get(
            "CATFLOW_OBJECT_STORAGE_ENDPOINT", DEFAULT_TOS_S3_ENDPOINT
        ).strip().rstrip("/")
        # 默认 public_endpoint 与 endpoint 相同(MinIO 等可分别配置)
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
        """8 个关键字段全填才算 configured。"""
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
        """内部上传 endpoint 的 host(用于 boto3 endpoint_url 解析)。"""
        return urlsplit(self.endpoint_url).hostname or ""

    @property
    def public_endpoint_host(self) -> str:
        """公开访问 endpoint 的 host(给 Ark 拉取的 host)。"""
        return urlsplit(self.public_endpoint_url).hostname or ""

    @property
    def public_object_host(self) -> str:
        """签发 URL 时 bucket 子域名。

        - virtual style:bucket 出现在子域名(bucket.s3.volces.com)
        - path style:bucket 出现在 path(/bucket/key)
        TOS 强制 virtual,所以默认返回子域名形式
        """
        if self.addressing_style == "virtual" and self.bucket:
            return f"{self.bucket}.{self.public_endpoint_host}"
        return self.public_endpoint_host


class S3ObjectStore:
    """S3-compatible 上传 / 校验 / 签发 / 删除的具体执行层。

    边界:
    - 不持有任何业务字段(只引用 settings)
    - 接受 4 个客户端注入(upload / public / http / host_resolver)便于测试 mock
    - 所有失败统一抛 ObjectPublisherError
    """

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
        """上传一个本地文件并立即做 HEAD 校验。

        流程:
        1. 校验 object_key 形态(相对 + 不含 ..)
        2. 检查源文件存在 + SHA256 与调用方提供的 expected 匹配
        3. upload_file(自动分块 + 多 part)
        4. verify_object(HEAD 校验 SHA256 + byte_size + 拿 ETag)

        Args:
            path: 源文件路径(本机 var/media/...)
            object_key: 目标 TOS key(相对桶根,如 "catflow/segment-references/<id>.mp4")
            expected_sha256: 期望的 SHA256,必须与源文件实际 SHA256 匹配

        Returns:
            StoredObject(包含 TOS 返回的 ETag)

        Raises:
            ObjectPublisherError: 源缺失、SHA256 不匹配、上传失败
        """
        _validate_object_key(object_key)
        # 1. 源文件存在性
        if not path.is_file():
            raise ObjectPublisherError("source_missing", "segment reference source is missing")
        byte_size = path.stat().st_size
        # 2. 源文件 SHA256 与 expected 必须一致(防 caller 算错 hash)
        if _sha256(path) != expected_sha256:
            raise ObjectPublisherError(
                "source_sha256_mismatch", "segment reference source SHA256 does not match"
            )
        # 3. 上传(ContentType=video/mp4 让 Ark 识别为视频;Metadata.sha256 便于后续 HEAD 校验)
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
        # 4. HEAD 校验(返回对象元数据,用于记录 ETag)
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
        """HEAD 校验 TOS 上对象的 SHA256 + 大小,返回 ETag。

        CatFlow 的"上传后立刻 verify"模式:防止 TOS 收到损坏字节(网络抖动)却被当作成功。
        """
        _validate_object_key(object_key)
        try:
            head = self._upload_client.head_object(
                Bucket=self.settings.bucket,
                Key=object_key,
            )
        except Exception as exc:
            raise _classify_s3_error("head", exc) from exc
        # boto3 返回 dict;Metadata 在用户自定义 metadata,ContentLength 是字节数
        metadata = head.get("Metadata") if isinstance(head, dict) else None
        remote_sha256 = metadata.get("sha256") if isinstance(metadata, dict) else None
        remote_size = head.get("ContentLength") if isinstance(head, dict) else None
        # SHA256 必须与本机一致(网络传输完整)
        if remote_sha256 != expected_sha256:
            raise ObjectPublisherError(
                "remote_sha256_mismatch", "published object SHA256 metadata does not match"
            )
        # 字节数必须一致
        if remote_size != expected_byte_size:
            raise ObjectPublisherError(
                "remote_size_mismatch", "published object byte size does not match"
            )
        # ETag 带双引号(ETag="..."),strip 后保留
        etag = str(head.get("ETag", "")).strip('"')
        return StoredObject(
            object_key=object_key,
            sha256=expected_sha256,
            byte_size=expected_byte_size,
            etag=etag,
        )

    def presign_get(self, object_key: str) -> str:
        """签发 2 小时 HTTPS GET URL(给 Ark 拉取本机 MP4)。

        安全约束:
        1. 校验 object_key 形态
        2. 强制 public_object_host 能 DNS 解析为公网 IP(防 host 替换攻击)
        3. 校验签出的 URL host / scheme 与配置一致(防 boto3 异常返回内网 URL)
        """
        _validate_object_key(object_key)
        self._require_public_dns()  # 防 ark 拉内网
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
        # 强制 URL host / scheme 与配置匹配(防 boto3 异常)
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
        """从(TOS 上删除对象(用于 check_roundtrip 清理)。"""
        _validate_object_key(object_key)
        try:
            self._upload_client.delete_object(Bucket=self.settings.bucket, Key=object_key)
        except Exception as exc:
            raise _classify_s3_error("delete", exc) from exc

    def head_bucket(self) -> None:
        """HEAD 桶:检查桶存在性 + 当前凭据可访问。

        CatFlow 在启动时调一次,失败则 publisher ready=false 但不阻塞后端启动。
        """
        self._require_public_dns()
        try:
            self._upload_client.head_bucket(Bucket=self.settings.bucket)
        except Exception as exc:
            raise _classify_s3_error("bucket", exc) from exc

    def check_roundtrip(self) -> PublisherCheckResult:
        """完整往返自检:上传 → HEAD → 预签名 → HTTP GET → 字节校验 → 删除。

        这是 publisher 唯一可靠的 ready 信号 —— 仅 HEAD bucket 不够,
        因为 HEAD 可能成功但网络路由或 DNS 仍有问题。
        调用 /api/v1/runtime/object-publisher/check 触发。
        """
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
        """DNS 解析 public_object_host,要求每个 IP 都是公网(防泄露内网)。

        拒绝:
        - 无法解析 / 解析结果为空 → public_host_resolution_failed
        - 解析到非 IP 字面值 → invalid_public_address
        - 任一 IP 非全球唯一(private / loopback / multicast 等) → private_publication_address

        这层保护关键,因为 CatFlow 强制 local-only / loopback-only,
        万一配置错误把内网 endpoint 暴露给 Ark,会泄露内网服务。
        """
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
    """进程级 publisher 状态机 —— 只暴露 ready / configured / 错误,绝不暴露凭据或签名 URL。

    启动时(check_on_start=True):
    1. settings.configured → false 则 _error=object_storage_not_configured,return
    2. 实例化 S3ObjectStore
    3. _check_bucket → HEAD bucket,成功则 _ready=True,失败则 _error+_ready=False

    check_roundtrip 可在 runtime 启动后任意时刻调用(/api/v1/runtime/object-publisher/check)
    """

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
        """创建一个"明确禁用"的 runtime,用于单测 / 配置缺失场景。"""
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
        """便捷工厂:从环境变量构造 + 默认启动检查。"""
        return cls(ObjectStorageSettings.from_env(), check_on_start=check_on_start)

    @property
    def status(self) -> ObjectPublisherStatus:
        """当前状态快照(供 /api/v1/runtime/bootstrap 返回)。"""
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
        """完整往返自检(覆盖上传 + HEAD + 预签名 GET + 删除)。

        失败设置 _ready=False 并记录 _error;成功设置 _ready=True。
        """
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
        """启动期 HEAD bucket 探测。失败不抛(仅记录 _error),让后端继续启动。"""
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


# ─────────────────── 模块级工具函数 ───────────────────

def _parse_endpoint(value: str, *, public: bool):
    """校验 endpoint URL 合法性:
    - 必须是绝对 URL(scheme://host)
    - 不能含 user:password@ / path / query / fragment
    - public=True:必须 HTTPS,且 host 必须可被解析为公网 IP(详见 _validate_public_host)
    - public=False:可 HTTPS,或 loopback HTTP(本地 MinIO)
    """
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
    """防"内网泄露"二次校验。

    拒绝:
    - *.ivolces.com(TOS VPC 内部域名) → VPC 泄露风险
    - localhost(无意义) → 必须用公网
    - 解析结果为 RFC1918 / loopback / link-local / multicast 等私有 IP → private IP 泄露风险
    """
    lowered = host.rstrip(".").lower()
    # *.ivolces.com 是 TOS 的 VPC 内部域名,不能给 Ark(可能在 Ark 服务端被拦截且泄露 VPC 拓扑)
    if lowered.endswith(".ivolces.com") or lowered == "ivolces.com":
        raise ValueError("object storage public endpoint cannot use a VPC host")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        # 不是 IP 字面值 → 是域名(hostname),只检查非 localhost
        if lowered == "localhost":
            raise ValueError(
                "object storage public endpoint must use a public host"
            ) from None
        return
    # IP 字面值:必须 is_global(Python 3.4+ 的 ipaddress 内部判定 RFC1918 / loopback)
    if not address.is_global:
        raise ValueError("object storage public endpoint must use a public IP address")


def _is_loopback_host(host: str) -> bool:
    """判定 host 是否回环地址(hostname=localhost 或 IP=127.x / ::1)。

    本机 MinIO(走 http://127.0.0.1:9000)用此判断。
    """
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_tos_s3_host(host: str) -> bool:
    """判定 host 是否 TOS S3 endpoint(用于强制 VirtualHostStyle)。

    TOS host 格式:*.tos-s3-<region>.volces.com(公开) 或 *.ivolces.com(VPC)
    """
    lowered = host.rstrip(".").lower()
    return ".tos-s3-" in f".{lowered}" and lowered.endswith((".volces.com", ".ivolces.com"))


def _new_s3_client(settings: ObjectStorageSettings, endpoint_url: str):
    """构造 boto3 S3 客户端(签名版本 s3v4,S3-compatible 标准)。

    Raises:
        ObjectPublisherError(s3_dependency_missing): boto3 未安装(应极少发生)
    """
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
        # signature_version="s3v4" 是 AWS 推荐,TOS 也支持
        # addressing_style 透传(virtual/path)
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.addressing_style},
        ),
    )


def _resolve_host(host: str) -> tuple[str, ...]:
    """DNS 解析 host → 排序去重后的 IPv4 列表。

    用于 _require_public_dns 检查公网 IP。
    """
    return tuple(
        sorted(
            {
                # getaddrinfo 返回 (family, type, proto, canonname, sockaddr),sockaddr[0] 是 IP
                result[4][0]
                for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            }
        )
    )


def _validate_object_key(value: str) -> None:
    """校验 object_key 形态 —— 防越界写入。

    拒绝:
    - 空字符串
    - 以 / 开头(绝对路径)
    - 任一段为 ..(上一级目录,允许越界)
    """
    if not value or value.startswith("/") or ".." in value.split("/"):
        raise ObjectPublisherError("invalid_object_key", "object key must be a relative exact key")


def _sha256(path: Path) -> str:
    """流式计算 SHA256(分块 1MB,避免大文件占内存)。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_sdk_message(exc: Exception) -> str:
    """SDK 异常的兜底消息(只暴露类名,避免泄漏 AKID / endpoint 等敏感字段)。"""
    name = type(exc).__name__
    return f"S3-compatible object operation failed ({name})"


def _classify_s3_error(operation: str, exc: Exception) -> ObjectPublisherError:
    """把 botocore SDK 异常分类成 8 类 ObjectPublisherError。

    分类优先级(自上而下):
    1. 网络层异常(EndpointConnection / ConnectTimeout / ReadTimeout / ConnectionClosed)
       → object_storage_network_error
    2. ClientError 且 Code ∈ 权限类(AccessDenied / Forbidden / InvalidAccessKeyId)
       → object_storage_permission_denied
    3. ClientError 且 Code ∈ 签名类
       (Malformed / InvalidSignature / RequestTimeTooSkewed / SignatureDoesNotMatch)
       → object_storage_signature_invalid
    4. HEAD 时 Code=404 / NoSuchKey → object_not_found
    5. 其他 ClientError → object_<operation>_failed
    """
    # 1. 网络层
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
    # 2-4. 服务端 ClientError
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", "")).lower()
        # 权限类:凭据无 IAM 权限或 AKID 错
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
        # 签名类:时区错位或 SecretKey 错
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
        # HEAD 找不到对象 → 对象已过期/未上传/被删
        if operation == "head" and code in {
            "404",
            "nosuchkey",
            "notfound",
        }:
            return ObjectPublisherError("object_not_found", "published object is absent")
    # 5. 兜底
    return ObjectPublisherError(f"object_{operation}_failed", _safe_sdk_message(exc))
