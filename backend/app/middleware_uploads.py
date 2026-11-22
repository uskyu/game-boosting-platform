"""/uploads 回源中间件：COS 模式下从对象存储读取，未迁移老文件回源本地磁盘。

背景：历史上所有上传都落在本地磁盘 ``UPLOAD_DIR``，由 ``StaticFiles`` 直接
服务；数据库里保存的是 ``/uploads/<key>`` 这类相对 URL。切到 COS 后同一
URL 契约不变，只是文件字节换了个存放位置，因此读路径必须按当前生效的存储
后端回源，而不改任何客户端/前端。

行为约定：
- LOCAL 模式：完全不介入，零行为变化（继续由 StaticFiles 服务）。
- COS 模式：先查 COS；命中即返回，附带 immutable 缓存头（文件名是 uuid，
  内容永不变化）。
- COS 未命中 -> 回源本地磁盘（部分迁移期间老图仍在本地，绝不能平白 404）。
- 两边都没有 -> 交给下游（StaticFiles）产生标准 404/405 响应。
- 配置 CDN 域名时直接 302 到 ``https://<cdn>/<key>``，省掉服务器带宽。

这个中间件自己开短会话取配置（不依赖 request.state），并在进入网络 I/O 前
关闭会话——否则一次几 MB 的图片回源会长时间占用一条数据库连接。
"""

from __future__ import annotations

import logging

from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services import storage_service

logger = logging.getLogger(__name__)

_UPLOADS_PREFIX = "/uploads/"
_IMMUTABLE_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


class UploadsStorageMiddleware:
    """在 StaticFiles 之前接管 /uploads/*，按存储模式回源。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(_UPLOADS_PREFIX) or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        try:
            key = storage_service.key_from_url(path)
        except ValueError:
            # 路径穿越/空 key：直接 404，不触碰磁盘或网络。
            await self._send_simple(scope, receive, send, 404, b"Not Found")
            return
        config = await self._resolve_config()
        if config is None or config.mode != "COS":
            # LOCAL 模式（或配置读取彻底失败）：交给原来的 StaticFiles。
            await self.app(scope, receive, send)
            return

        # 配置了 CDN 域名：直接重定向，服务器不代理字节流。
        if config.cdn_domain:
            location = f"https://{config.cdn_domain}/{key}"
            response = RedirectResponse(location, status_code=302)
            await response(scope, receive, send)
            return

        try:
            payload = await storage_service.read_bytes_for_config(key, config)
            if payload is None:
                payload = await storage_service.read_local_bytes(key)
        except storage_service.StorageError:
            # COS 侧报错（网络/鉴权）：仍尝试本地兜底，避免老图全部挂掉。
            logger.warning("COS uploads fetch failed, falling back to local: %s", key)
            try:
                payload = await storage_service.read_local_bytes(key)
            except Exception:  # noqa: BLE001 - 兜底失败按不存在处理
                payload = None
        except Exception:  # noqa: BLE001 - key 已校验，其余都是存储侧异常
            logger.exception("Unexpected uploads fetch failure: %s", key)
            payload = None

        if payload is None:
            await self.app(scope, receive, send)
            return

        data, content_type = payload
        headers = dict(_IMMUTABLE_CACHE_HEADERS)
        if scope.get("method") == "HEAD":
            # HEAD 只需头部：给出正确长度但不回传正文，避免多传一遍字节。
            headers["Content-Type"] = content_type or "application/octet-stream"
            headers["Content-Length"] = str(len(data))
            response = Response(content=b"", status_code=200, headers=headers)
        else:
            response = Response(
                content=data,
                media_type=content_type or "application/octet-stream",
                headers=headers,
            )
        logger.debug("Served upload from %s: %s (%d bytes)", config.mode, key, len(data))
        await response(scope, receive, send)

    async def _resolve_config(self) -> storage_service.StorageConfig | None:
        """取当前存储配置：先读进程内短缓存，未命中再开短会话查一次。"""
        cached = storage_service.peek_storage_config()
        if cached is not None:
            return cached

        from app.db.session import async_session_factory

        try:
            async with async_session_factory() as session:
                return await storage_service.get_storage_config(session)
        except Exception:  # noqa: BLE001 - DB 故障时退回 StaticFiles
            logger.warning("Storage config lookup failed for /uploads", exc_info=True)
            return None

    async def _send_simple(
        self, scope: Scope, receive: Receive, send: Send, status_code: int, body: bytes
    ) -> None:
        response = Response(content=body, status_code=status_code, media_type="text/plain")
        await response(scope, receive, send)
