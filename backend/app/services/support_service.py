"""
AI customer support service.
Handles template-based quick replies and DeepSeek-powered smart responses.
"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

SUPPORT_SYSTEM_PROMPT = """你是「游戏代练平台」的智能客服助手。你的职责是帮助用户解决平台使用相关问题。

回复要求：
1. 用中文回答，语气友好专业
2. 回答简洁明了，不超过200字
3. 返回纯JSON格式，包含以下字段：
   - reply: 你的回复文本
   - category: 问题分类 (order/refund/complaint/account/general)
   - actions: 建议操作列表，每个操作包含 {label, type, link}
     - type可选: navigate(页面跳转), transfer(转人工), action(执行操作)
   - need_human: 布尔值，是否建议转人工客服

常见场景处理：
- 订单问题：引导查看订单详情，提供订单链接
- 退款售后：说明退款流程，引导联系管理员或发起争议
- 代练投诉：引导发起订单争议，建议转人工处理
- 账户问题：引导前往个人中心或设置页面
- 代练申请：引导前往个人中心提交申请
- 其他问题：尽力回答，复杂问题建议转人工"""

# Pre-built template categories and quick replies
TEMPLATE_CATEGORIES: list[dict[str, Any]] = [
    {
        "key": "order",
        "label": "订单问题",
        "icon": "📋",
        "templates": [
            {"key": "order_status", "text": "我的订单状态是什么？"},
            {"key": "order_slow", "text": "代练进度太慢了怎么办？"},
            {"key": "order_cancel", "text": "我想取消订单"},
            {"key": "order_account", "text": "代练需要我的游戏账号吗？"},
        ],
    },
    {
        "key": "refund",
        "label": "退款/售后",
        "icon": "💰",
        "templates": [
            {"key": "refund_how", "text": "怎么申请退款？"},
            {"key": "refund_time", "text": "退款多久到账？"},
            {"key": "refund_partial", "text": "可以部分退款吗？"},
            {"key": "order_done_wrong", "text": "代练完成了但结果不对"},
        ],
    },
    {
        "key": "complaint",
        "label": "代练投诉",
        "icon": "⚠️",
        "templates": [
            {"key": "booster_cheat", "text": "怀疑代练使用外挂"},
            {"key": "booster_noshow", "text": "代练接单后不做了"},
            {"key": "booster_rude", "text": "代练态度恶劣"},
            {"key": "account_issue", "text": "代练把我号搞出问题了"},
        ],
    },
    {
        "key": "account",
        "label": "账户相关",
        "icon": "👤",
        "templates": [
            {"key": "become_booster", "text": "我想成为代练"},
            {"key": "change_password", "text": "怎么修改密码？"},
            {"key": "profile_settings", "text": "怎么修改个人资料？"},
            {"key": "notification_settings", "text": "怎么设置通知偏好？"},
        ],
    },
]

# Fast local responses for template questions (no API call needed)
TEMPLATE_RESPONSES: dict[str, dict[str, Any]] = {
    "order_status": {
        "reply": "您可以在「我的订单」页面查看所有订单状态。订单状态包括：待接单、进行中、待确认、已完成、争议中、已取消。",
        "category": "order",
        "actions": [
            {"label": "查看我的订单", "type": "navigate", "link": "/orders"},
        ],
        "need_human": False,
    },
    "order_slow": {
        "reply": "如果代练进度太慢，建议您先通过聊天功能联系代练沟通。如果沟通无果，可以发起订单争议，管理员会介入处理。",
        "category": "order",
        "actions": [
            {"label": "查看我的订单", "type": "navigate", "link": "/orders"},
            {"label": "联系客服", "type": "transfer", "link": ""},
        ],
        "need_human": False,
    },
    "order_cancel": {
        "reply": "待接单状态的订单可以直接取消。已接单的订单需要通过发起争议来处理取消。请前往订单详情页面操作。",
        "category": "order",
        "actions": [
            {"label": "查看我的订单", "type": "navigate", "link": "/orders"},
        ],
        "need_human": False,
    },
    "order_account": {
        "reply": "是的，部分代练服务需要您提供游戏账号信息。您的账号信息仅对接单代练和管理员可见，其他用户看不到。建议在订单开始前与代练确认具体需求。",
        "category": "order",
        "actions": [],
        "need_human": False,
    },
    "refund_how": {
        "reply": "退款流程：1) 如果订单未接单，直接取消即可；2) 已接单的订单，请先发起争议；3) 管理员审核后可为您退款。已支付且处于取消/争议状态的订单，管理员可操作退款。",
        "category": "refund",
        "actions": [
            {"label": "查看我的订单", "type": "navigate", "link": "/orders"},
            {"label": "联系客服", "type": "transfer", "link": ""},
        ],
        "need_human": False,
    },
    "refund_time": {
        "reply": "退款通常在管理员确认后立即处理。具体到账时间取决于支付渠道，一般1-3个工作日。如有疑问可联系人工客服。",
        "category": "refund",
        "actions": [
            {"label": "联系客服", "type": "transfer", "link": ""},
        ],
        "need_human": False,
    },
    "refund_partial": {
        "reply": "目前平台支持全额退款。如需协商部分退款，建议发起争议后由管理员根据实际情况处理。",
        "category": "refund",
        "actions": [
            {"label": "联系客服", "type": "transfer", "link": ""},
        ],
        "need_human": True,
    },
    "order_done_wrong": {
        "reply": "如果代练完成结果与预期不符，请立即发起订单争议（在订单详情页点击「发起争议」），管理员会介入核实处理。",
        "category": "refund",
        "actions": [
            {"label": "查看我的订单", "type": "navigate", "link": "/orders"},
            {"label": "联系客服", "type": "transfer", "link": ""},
        ],
        "need_human": True,
    },
    "booster_cheat": {
        "reply": "使用外挂是严重违规行为。请立即发起订单争议并在争议说明中注明怀疑使用外挂。我们会调查处理，严重者将封禁代练账号。建议转接人工客服协助处理。",
        "category": "complaint",
        "actions": [
            {"label": "转人工客服", "type": "transfer", "link": ""},
        ],
        "need_human": True,
    },
    "booster_noshow": {
        "reply": "代练接单后不履行的情况，您可以发起订单争议。管理员确认后会取消订单并对代练进行信誉扣分处理。",
        "category": "complaint",
        "actions": [
            {"label": "查看我的订单", "type": "navigate", "link": "/orders"},
            {"label": "转人工客服", "type": "transfer", "link": ""},
        ],
        "need_human": True,
    },
    "booster_rude": {
        "reply": "非常抱歉您遇到了不好的体验。请将聊天记录截图并发起订单争议，管理员会对代练的行为进行处理。",
        "category": "complaint",
        "actions": [
            {"label": "转人工客服", "type": "transfer", "link": ""},
        ],
        "need_human": True,
    },
    "account_issue": {
        "reply": "如果您的游戏账号出现异常（如封禁、数据丢失等），请立即发起订单争议并联系人工客服。我们将全力协助您解决问题。",
        "category": "complaint",
        "actions": [
            {"label": "转人工客服", "type": "transfer", "link": ""},
        ],
        "need_human": True,
    },
    "become_booster": {
        "reply": "想成为代练？请前往「个人中心」，在页面下方提交代练申请。您需要填写擅长的游戏、当前和目标段位，并上传游戏截图作为证明。审核通过后即可开始接单。",
        "category": "account",
        "actions": [
            {"label": "前往个人中心", "type": "navigate", "link": "/profile"},
        ],
        "need_human": False,
    },
    "change_password": {
        "reply": "修改密码：前往「个人中心」，在密码部分输入当前密码和新密码即可。新密码需至少8位，包含大写字母和数字。",
        "category": "account",
        "actions": [
            {"label": "前往个人中心", "type": "navigate", "link": "/profile"},
        ],
        "need_human": False,
    },
    "profile_settings": {
        "reply": "您可以在「个人中心」修改昵称、手机号和个人简介。前往个人中心即可编辑。",
        "category": "account",
        "actions": [
            {"label": "前往个人中心", "type": "navigate", "link": "/profile"},
        ],
        "need_human": False,
    },
    "notification_settings": {
        "reply": "通知偏好设置：前往「设置」页面，在「通知偏好」选项卡中可以开关各类通知。您也可以管理隐私和缓存。",
        "category": "account",
        "actions": [
            {"label": "前往设置", "type": "navigate", "link": "/settings"},
        ],
        "need_human": False,
    },
}


class SupportService:
    """AI customer support logic."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )

    def get_templates(self) -> list[dict[str, Any]]:
        """Return template categories and quick-reply options."""
        return TEMPLATE_CATEGORIES

    def get_template_response(self, template_key: str) -> dict[str, Any] | None:
        """Return a pre-built response for a template key."""
        return TEMPLATE_RESPONSES.get(template_key)

    async def get_ai_response(self, message: str) -> dict[str, Any]:
        """Call DeepSeek for a free-form customer question."""
        try:
            response = await self._client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": SUPPORT_SYSTEM_PROMPT},
                    {"role": "user", "content": message.strip()},
                ],
                temperature=0.3,
                max_tokens=512,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                return self._fallback_response()

            parsed = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
            # Ensure required fields
            return {
                "reply": parsed.get("reply", "抱歉，我暂时无法回答这个问题。"),
                "category": parsed.get("category", "general"),
                "actions": parsed.get("actions", []),
                "need_human": parsed.get("need_human", False),
            }
        except Exception:
            logger.exception("AI support response failed")
            return self._fallback_response()

    def _fallback_response(self) -> dict[str, Any]:
        return {
            "reply": "抱歉，AI客服暂时无法回答。建议您转接人工客服获取帮助。",
            "category": "general",
            "actions": [
                {"label": "转人工客服", "type": "transfer", "link": ""},
            ],
            "need_human": True,
        }


_support_service: SupportService | None = None


def get_support_service() -> SupportService:
    """Singleton factory."""
    global _support_service
    if _support_service is None:
        _support_service = SupportService()
    return _support_service
