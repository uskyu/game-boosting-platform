"""Shared response serializers.

数据库统一存 naive UTC（MySQL 会话时区固定 +00:00）。所有响应模型里的
datetime 字段都应通过 field_serializer 调用这里输出的带 Z 的 UTC 字符串，
否则浏览器会把裸时间按本地时区解读，产生 8 小时偏差。
"""

from datetime import datetime, timezone


def serialize_datetime_utc(value: datetime | None) -> str | None:
    """Emit an explicit UTC instant with Z suffix, or None."""
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
