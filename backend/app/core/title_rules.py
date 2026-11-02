"""订单标题关键词替换规则。

后台可在「站点设置 → 订单标题关键词替换」里维护规则，默认把
「名牌」「明牌」替换为「【MP】」，避免订单大厅/聊天订单卡片出现
容易触发平台审核的敏感词。

规则存为 ``site_settings.order_title_replacements`` JSON 数组：
``[{"original": "名牌", "replacement": "【MP】"}, ...]``。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

DEFAULT_ORDER_TITLE_REPLACEMENTS: list[dict[str, str]] = [
    {"original": "名牌", "replacement": "【MP】"},
    {"original": "明牌", "replacement": "【MP】"},
]

MAX_RULES = 50
MAX_TITLE_LENGTH = 200


def normalize_rules(raw: Iterable[Any] | None) -> list[dict[str, str]]:
    """把数据库/接口里的规则统一成 [{original, replacement}]。

    - None → 默认规则（从未配置）
    - []   → 管理员明确关闭全部替换
    - 过滤空原文、去重，保留首次出现的顺序
    """
    if raw is None:
        return [dict(rule) for rule in DEFAULT_ORDER_TITLE_REPLACEMENTS]

    rules: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            original = str(item.get("original") or "")
            replacement = str(item.get("replacement") or "")
        else:
            original = str(getattr(item, "original", "") or "")
            replacement = str(getattr(item, "replacement", "") or "")
        original = original.strip()
        if not original or original in seen:
            continue
        seen.add(original)
        rules.append({"original": original, "replacement": replacement})
        if len(rules) >= MAX_RULES:
            break
    return rules


def apply_title_replacements(
    title: str | None,
    rules: Iterable[Any] | None,
    *,
    max_length: int = MAX_TITLE_LENGTH,
) -> str | None:
    """按规则替换标题中的关键词（单次替换，长词优先）。

    - 替换结果为空时保留原标题，避免产生空标题；
    - 超过数据库 String(200) 时截断，避免严格模式下写库失败；
    - 替换文本按字面值使用，不会被当成正则反向引用。
    """
    if title is None:
        return None

    normalized = normalize_rules(rules)
    if not normalized:
        return title[:max_length]

    ordered = sorted(normalized, key=lambda rule: len(rule["original"]), reverse=True)
    mapping = {rule["original"]: rule["replacement"] for rule in ordered}
    pattern = re.compile("|".join(re.escape(word) for word in mapping))
    replaced = pattern.sub(lambda match: mapping[match.group(0)], title)
    if not replaced.strip():
        replaced = title
    return replaced[:max_length]
