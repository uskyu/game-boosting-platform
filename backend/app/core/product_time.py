"""产品时区（Asia/Shanghai，固定 +08:00）的纯时间工具。

部署用 Asia/Shanghai，而后端镜像不带可选的系统 tzdata 包，因此这里用
固定 +08:00 偏移——该产品时区没有夏令时，固定偏移不会算错，午夜/正午
的规则也能保持确定性。

数据库里的时间列（created_at 等）一律是 naive UTC，因此做区间比较的
函数都返回 naive UTC，可以直接与库里值比较。
"""

from datetime import date, datetime, time, timedelta, timezone

# 固定 +08:00，与 announcements 原先的本地定义保持同一来源。
PRODUCT_TIMEZONE = timezone(timedelta(hours=8))


def _as_aware(moment: datetime) -> datetime:
    """把（可能带或不带 tzinfo 的）时刻统一成 aware UTC。"""
    aware = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
    return aware.astimezone(timezone.utc)


def product_local_date(moment: datetime) -> date:
    """moment 所在的产品时区自然日。"""
    return _as_aware(moment).astimezone(PRODUCT_TIMEZONE).date()


def product_day_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """moment 所在产品自然日的 [start, end)，返回 naive UTC。"""
    local_midnight = datetime.combine(
        product_local_date(moment), time.min, tzinfo=PRODUCT_TIMEZONE
    )
    start = local_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1)


def product_noon_utc(day: date) -> datetime:
    """该产品日 12:00 对应的 naive UTC 时刻。"""
    local_noon = datetime(
        day.year, day.month, day.day, 12, 0, tzinfo=PRODUCT_TIMEZONE
    )
    return local_noon.astimezone(timezone.utc).replace(tzinfo=None)


def product_wall_text(value: datetime | None) -> str:
    """naive UTC → 产品时区墙钟 "MM-DD HH:mm"（None → 空串），用于错误文案。"""
    if value is None:
        return ""
    wall = _as_aware(value).astimezone(PRODUCT_TIMEZONE)
    return wall.strftime("%m-%d %H:%M")
