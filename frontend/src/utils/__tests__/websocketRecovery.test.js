import { describe, expect, it } from 'vitest'

import {
  getWebSocketHealthAction,
  getWebSocketReconnectDelay,
  WS_CONNECTING_TIMEOUT_MS,
  WS_IMMEDIATE_RECONNECT_DELAY_MS,
  WS_MAX_RECONNECT_DELAY_MS,
  WS_STALE_TIMEOUT_MS,
} from '../websocketRecovery'

const OPEN = 1
const CONNECTING = 0
const CLOSING = 2

function health(overrides = {}) {
  return getWebSocketHealthAction({
    readyState: OPEN,
    openState: OPEN,
    connectingState: CONNECTING,
    closingState: CLOSING,
    authenticated: true,
    socketStartedAt: 10_000,
    lastPongAt: 50_000,
    now: 60_000,
    ...overrides,
  })
}

describe('WebSocket half-open recovery policy', () => {
  it('keeps an authenticated open socket while pong is fresh', () => {
    expect(health()).toBe('healthy')
  })

  it('replaces an open socket when pong has gone stale', () => {
    expect(health({ now: 50_000 + WS_STALE_TIMEOUT_MS + 1 })).toBe('replace')
  })

  it('does not treat a recently opened socket as half-open before auth timeout', () => {
    expect(health({
      readyState: OPEN,
      authenticated: false,
      socketStartedAt: 10_000,
      now: 10_000 + WS_CONNECTING_TIMEOUT_MS - 1,
    })).toBe('authenticating')
  })

  it('replaces a socket stuck in CONNECTING after its timeout', () => {
    expect(health({
      readyState: CONNECTING,
      authenticated: false,
      socketStartedAt: 10_000,
      now: 10_000 + WS_CONNECTING_TIMEOUT_MS + 1,
    })).toBe('replace')
  })

  it('replaces a socket stuck in CLOSING instead of waiting for a missing close event', () => {
    expect(health({ readyState: CLOSING })).toBe('replace')
  })
})

describe('WebSocket reconnect backoff', () => {
  it('uses bounded exponential delay and caps at 30 seconds', () => {
    expect([0, 1, 2, 3, 4, 5, 6].map((attempt) => getWebSocketReconnectDelay(attempt)))
      .toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000])
    expect(WS_MAX_RECONNECT_DELAY_MS).toBe(30000)
  })

  it('uses one short delay for a confirmed stale socket recovery', () => {
    expect(getWebSocketReconnectDelay(6, { immediate: true }))
      .toBe(WS_IMMEDIATE_RECONNECT_DELAY_MS)
  })
})
