"""Shared HTTP header validation."""


def parse_version_header(value: str) -> int:
    normalized = value.strip().strip('"')
    try:
        version = int(normalized)
    except ValueError as exc:
        raise ValueError("If-Match 必须是画布或对象的整数版本") from exc
    if version < 0:
        raise ValueError("If-Match 版本不能为负数")
    return version
