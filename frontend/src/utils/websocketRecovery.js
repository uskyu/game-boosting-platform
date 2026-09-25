export const WS_HEARTBEAT_INTERVAL_MS = 20_000
export const WS_STALE_TIMEOUT_MS = 45_000
export const WS_WATCHDOG_INTERVAL_MS = 5_000
export const WS_CONNECTING_TIMEOUT_MS = 15_000
export const WS_IMMEDIATE_RECONNECT_DELAY_MS = 250
export const WS_MAX_RECONNECT_DELAY_MS = 30_000

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
