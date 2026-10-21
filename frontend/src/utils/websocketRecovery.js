export const WS_HEARTBEAT_INTERVAL_MS = 20_000
export const WS_STALE_TIMEOUT_MS = 45_000
export const WS_WATCHDOG_INTERVAL_MS = 5_000
export const WS_CONNECTING_TIMEOUT_MS = 15_000
export const WS_IMMEDIATE_RECONNECT_DELAY_MS = 250
export const WS_MAX_RECONNECT_DELAY_MS = 30_000

// 服务端因会话失效主动关闭连接时使用的自定义关闭码（4000-4999 为应用层
// 保留区间）。收到它表示"这个账号的登录状态已被作废"（改密码/被重置密码
// 后其他设备会遇到），客户端不应重连，直接按登录失效处理。
export const WS_CLOSE_SESSION_INVALIDATED = 4001

export function getWebSocketHealthAction({
  readyState,
  openState,
  connectingState,
  closingState,
  authenticated,
  socketStartedAt,
  lastPongAt,
  now,
}) {
  if (readyState === closingState) return 'replace'

  if (readyState === connectingState) {
    return now - socketStartedAt > WS_CONNECTING_TIMEOUT_MS ? 'replace' : 'connecting'
  }

  if (readyState === openState) {
    if (!authenticated) {
      return now - socketStartedAt > WS_CONNECTING_TIMEOUT_MS ? 'replace' : 'authenticating'
    }
    if (!lastPongAt || now - lastPongAt > WS_STALE_TIMEOUT_MS) return 'replace'
    return 'healthy'
  }

  return 'connect'
}

export function getWebSocketReconnectDelay(attempt, { immediate = false } = {}) {
  if (immediate) return WS_IMMEDIATE_RECONNECT_DELAY_MS
  return Math.min(1000 * (2 ** Math.max(0, attempt)), WS_MAX_RECONNECT_DELAY_MS)
}
