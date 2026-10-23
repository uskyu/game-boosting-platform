#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting backend service..."
# 注意：这里**不要**加 --forwarded-allow-ips=*。
#
# uvicorn 的 ProxyHeadersMiddleware 在 always_trust 模式下取
# X-Forwarded-For 的**最左**值作为 client，而最左值完全由客户端控制
# （nginx 的 $proxy_add_x_forwarded_for 是把真实 IP 追加到右侧）。一旦放开，
# 攻击者每次轮换 XFF 首段就能把 request.client.host 变成任意值，使按 IP 的
# 限流彻底失效——比原 bug 更危险。
#
# 真实客户端 IP 交给应用层 app.core.client_ip 解析：它把「直连对端」
# （容器私网 IP，落在可信段）当可信代理，再从 XFF 右往左跳过可信代理取
# 第一个不可信地址，因此客户端伪造的首段会被忽略。
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
