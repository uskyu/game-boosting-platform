#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting backend service..."
# --forwarded-allow-ips：后端跑在容器里，直连对端是前端容器的 docker 内网
# 地址（如 10.211.0.4），而 uvicorn 默认只信任 127.0.0.1，会整体忽略
# X-Forwarded-For / X-Forwarded-Proto，导致 request.client.host 恒为容器 IP、
# 限流全站共用一个桶（2026-10-23 抢单高峰验证码 429 事故）。
#
# 应用层已用 app.core.client_ip 自行回溯真实 IP（不依赖这里的开关），
# 这里一并放开使 scheme/client 在整条中间件链上保持一致。
# 安全性由「backend 不发布宿主机端口、唯一入口是前端 nginx」保证。
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --forwarded-allow-ips="*"
