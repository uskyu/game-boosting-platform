import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
vi.mock('@/utils/api', () => ({
  default: { get: (...args) => apiGet(...args), post: (...args) => apiPost(...args) },
  refreshAccessToken: vi.fn(),
}))
vi.mock('@/utils/sound', () => ({
  playChatMessage: vi.fn(),
  playClaimed: vi.fn(),
  playNewOrder: vi.fn(),
}))

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'

const owner = { id: 1, username: 'Lin', role: 'USER' }
const booster = { id: 2, username: 'BoostKing', role: 'BOOSTER' }
const admin = { id: 3, username: '平台客服', role: 'ADMIN' }

function stubBrowserGlobals() {
  vi.stubGlobal('location', { protocol: 'https:', host: 'example.test' })
  vi.stubGlobal('document', { visibilityState: 'visible' })
  const windowListeners = new Map()
  vi.stubGlobal('window', {
    setInterval: (...args) => globalThis.setInterval(...args),
    clearInterval: (...args) => globalThis.clearInterval(...args),
    setTimeout: (...args) => globalThis.setTimeout(...args),
    clearTimeout: (...args) => globalThis.clearTimeout(...args),
    addEventListener: (name, listener) => windowListeners.set(name, listener),
    removeEventListener: (name, listener) => windowListeners.delete(name, listener),
    dispatchEvent: (event) => windowListeners.get(event.type)?.(event),
  })
  vi.stubGlobal('localStorage', {
    values: new Map(),
    getItem(key) { return this.values.get(key) ?? null },
    setItem(key, value) { this.values.set(key, String(value)) },
    removeItem(key) { this.values.delete(key) },
  })
}

function conversationPayload(overrides = {}) {
  return {
    id: 42,
    type: 'PRIVATE',
    participants: [
      { user_id: owner.id, role_snapshot: 'USER', user: owner },
      { user_id: booster.id, role_snapshot: 'BOOSTER', user: booster },
    ],
    other_participants: [booster],
    counterpart: booster,
    ...overrides,
  }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  stubBrowserGlobals()
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.setTokens('test-access-token', 'test-refresh-token')
  auth.setUser({ id: owner.id, role: 'USER' })
  globalThis.__testPinia = pinia
})

afterEach(() => {
  disposePinia(globalThis.__testPinia)
  delete globalThis.__testPinia
  vi.unstubAllGlobals()
})

describe('chat store counterpart merge', () => {
  it('空 other_participants 不覆盖已有对方名字', async () => {
    const chat = useChatStore()
    apiGet.mockResolvedValueOnce({ data: conversationPayload() })
    await chat.fetchConversation(42)
    expect(chat.conversations[0].other_participants.map((item) => item.username)).toEqual(['BoostKing'])

    // 服务端偶发返回空数组（例如广播后的增量刷新）：不能把名字抹掉
    apiGet.mockResolvedValueOnce({
      data: conversationPayload({ other_participants: [], counterpart: null }),
    })
    await chat.fetchConversation(42)

    expect(chat.conversations[0].other_participants.map((item) => item.username)).toEqual(['BoostKing'])
  })

  it('admin_joined 不会把当前登录用户自己写进 other_participants', async () => {
    const chat = useChatStore()
    const auth = useAuthStore()
    auth.setUser({ id: admin.id, role: 'ADMIN' })

    apiGet.mockResolvedValueOnce({
      data: conversationPayload({
        participants: [
          { user_id: owner.id, role_snapshot: 'USER', user: owner },
          { user_id: admin.id, role_snapshot: 'ADMIN', user: admin },
        ],
        other_participants: [owner],
        counterpart: owner,
      }),
    })
    await chat.fetchConversation(42)

    apiPost.mockResolvedValueOnce({
      data: { message: '管理员已加入会话', success: true, admin },
    })
    const result = await chat.inviteAdmin(42)

    expect(result.success).toBe(true)
    expect(chat.conversations[0].other_participants.map((item) => item.id)).toEqual([owner.id])
  })

  it('其他管理员加入时仍会追加到 other_participants', async () => {
    const chat = useChatStore()
    apiGet.mockResolvedValueOnce({ data: conversationPayload() })
    await chat.fetchConversation(42)

    apiPost.mockResolvedValueOnce({
      data: { message: '管理员已加入会话', success: true, admin },
    })
    await chat.inviteAdmin(42)

    expect(chat.conversations[0].other_participants.map((item) => item.id)).toEqual([booster.id, admin.id])
  })
})
