import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
vi.mock('@/utils/api', () => ({
  default: { get: (...args) => apiGet(...args) },
}))
vi.mock('@/utils/sound', () => ({ playChatMessage: vi.fn() }))

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'

class FakeWebSocket {
  static failNextConstruction = false
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances = []

  constructor(url) {
    if (FakeWebSocket.failNextConstruction) {
      FakeWebSocket.failNextConstruction = false
      throw new Error('mock connection construction failure')
    }
    this.url = url
    this.readyState = FakeWebSocket.CONNECTING
    this.sent = []
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    this.deferCloseEvent = false
    // Vue treats native WebSocket instances as built-ins and does not proxy them.
    // Make the fake non-extensible to preserve the same identity semantics.
    Object.preventExtensions(this)
    FakeWebSocket.instances.push(this)
  }

  send(data) {
    if (this.readyState !== FakeWebSocket.OPEN) throw new Error('socket is not open')
    this.sent.push(JSON.parse(data))
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.({})
  }

  message(event, data = {}) {
    this.onmessage?.({ data: JSON.stringify({ event, data }) })
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return
    this.readyState = FakeWebSocket.CLOSING
    const finish = () => {
      this.readyState = FakeWebSocket.CLOSED
      this.onclose?.({ code: 1000 })
    }
    if (this.deferCloseEvent) setTimeout(finish, 100)
    else finish()
  }
}

function flushMicrotasks() {
  return Promise.resolve().then(() => Promise.resolve())
}

function connectAndAuthenticate(chat) {
  chat.connectWebSocket()
  const ws = FakeWebSocket.instances.at(-1)
  ws.open()
  ws.message('auth_ok', { user_id: 1 })
  return ws
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-25T00:00:00Z'))
  FakeWebSocket.instances = []
  apiGet.mockReset().mockResolvedValue({ data: { total_unread: 0, items: [] } })
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('location', { protocol: 'https:', host: 'example.test' })
  vi.stubGlobal('document', { visibilityState: 'visible' })
  const windowListeners = new Map()
  vi.stubGlobal('window', {
    setInterval: (...args) => globalThis.setInterval(...args),
    clearInterval: (...args) => globalThis.clearInterval(...args),
    setTimeout: (...args) => globalThis.setTimeout(...args),
    clearTimeout: (...args) => globalThis.clearTimeout(...args),
    addEventListener: (name, listener) => windowListeners.set(name, listener),
    removeEventListener: (name) => windowListeners.delete(name),
    dispatchEvent: (event) => windowListeners.get(event.type)?.(event),
  })
  globalThis.__windowListeners = windowListeners
  vi.stubGlobal('localStorage', {
    values: new Map(),
    getItem(key) { return this.values.get(key) ?? null },
    setItem(key, value) { this.values.set(key, String(value)) },
    removeItem(key) { this.values.delete(key) },
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.setTokens('test-access-token', 'test-refresh-token')
  auth.setUser({ id: 1, role: 'BOOSTER' })
  globalThis.__testPinia = pinia
})

afterEach(() => {
  const chat = useChatStore()
  chat.disconnectWebSocket({ clearState: true })
  disposePinia(globalThis.__testPinia)
  delete globalThis.__testPinia
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('chat WebSocket recovery', () => {
  it('sends heartbeat and keeps a socket alive when pong arrives', async () => {
    const chat = useChatStore()
    const ws = connectAndAuthenticate(chat)

    await vi.advanceTimersByTimeAsync(20_000)
    expect(ws.sent.some((frame) => frame.event === 'ping')).toBe(true)
    ws.message('pong')
    await vi.advanceTimersByTimeAsync(40_000)

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(chat.socketStatus).toBe('connected')
  })

  it('closes a half-open socket after missed pong and reconnects once with auth', async () => {
    const chat = useChatStore()
    const original = connectAndAuthenticate(chat)

    await vi.advanceTimersByTimeAsync(50_000)
    expect(original.readyState).toBe(FakeWebSocket.CLOSED)
    expect(FakeWebSocket.instances).toHaveLength(2)

    const replacement = FakeWebSocket.instances[1]
    replacement.open()
    expect(replacement.sent).toEqual([
      { event: 'auth', data: { token: 'test-access-token' } },
    ])
    replacement.message('auth_ok', { user_id: 1 })
    expect(chat.socketStatus).toBe('connected')
  })

  it('does not declare a background-frozen socket stale, then recovers it on resume', async () => {
    const chat = useChatStore()
    const original = connectAndAuthenticate(chat)

    document.visibilityState = 'hidden'
    // 页面隐藏时浏览器会冻结 interval；模拟墙钟前进但不运行前端 timers。
    vi.setSystemTime(Date.now() + 90_000)
    expect(original.readyState).toBe(FakeWebSocket.OPEN)
    expect(FakeWebSocket.instances).toHaveLength(1)

    document.visibilityState = 'visible'
    chat.recoverWebSocketIfStale()
    expect(original.readyState).toBe(FakeWebSocket.CLOSED)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('coalesces repeated recovery signals while replacement is connecting', async () => {
    const chat = useChatStore()
    connectAndAuthenticate(chat)

    // Silence pong by moving time beyond the stale threshold without running watchdog.
    await vi.advanceTimersByTimeAsync(45_001)
    chat.recoverWebSocketIfStale()
    chat.recoverWebSocketIfStale()
    chat.recoverWebSocketIfStale()
    await flushMicrotasks()

    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('coalesces focus and online recovery signals into one replacement socket', async () => {
    const chat = useChatStore()
    const original = connectAndAuthenticate(chat)
    vi.setSystemTime(Date.now() + 60_000)

    chat.recoverWebSocketIfStale()
    chat.recoverWebSocketIfStale()
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)

    expect(original.readyState).toBe(FakeWebSocket.CLOSED)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('ignores online while hidden and recovers once when visible', async () => {
    const chat = useChatStore()
    const original = connectAndAuthenticate(chat)
    vi.setSystemTime(Date.now() + 60_000)

    document.visibilityState = 'hidden'
    window.dispatchEvent(new Event('online'))
    expect(FakeWebSocket.instances).toHaveLength(1)

    document.visibilityState = 'visible'
    window.dispatchEvent(new Event('focus'))
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)

    expect(original.readyState).toBe(FakeWebSocket.CLOSED)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('ignores a delayed close event from the replaced socket', async () => {
    const chat = useChatStore()
    const original = connectAndAuthenticate(chat)
    original.deferCloseEvent = true
    vi.setSystemTime(Date.now() + 60_000)

    chat.recoverWebSocketIfStale()
    expect(FakeWebSocket.instances).toHaveLength(2)
    const replacement = FakeWebSocket.instances[1]
    replacement.open()
    replacement.message('auth_ok', { user_id: 1 })
    expect(chat.socketStatus).toBe('connected')

    await vi.advanceTimersByTimeAsync(100)
    expect(chat.socket).toBe(replacement)
    expect(chat.socketStatus).toBe('connected')
  })

  it('releases recovery guard and schedules backoff if socket construction throws', async () => {
    const chat = useChatStore()
    const original = connectAndAuthenticate(chat)
    vi.setSystemTime(Date.now() + 60_000)
    FakeWebSocket.failNextConstruction = true

    chat.recoverWebSocketIfStale()
    expect(original.readyState).toBe(FakeWebSocket.CLOSED)
    expect(FakeWebSocket.instances).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('does not reconnect after explicit logout/disconnect', async () => {
    const chat = useChatStore()
    const ws = connectAndAuthenticate(chat)
    chat.disconnectWebSocket()

    await vi.advanceTimersByTimeAsync(120_000)
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(chat.socketStatus).toBe('disconnected')
  })
})
