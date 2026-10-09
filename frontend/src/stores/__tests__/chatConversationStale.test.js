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

beforeEach(() => {
  apiGet.mockReset().mockResolvedValue({ data: { total_unread: 0, items: [] } })
  apiPost.mockReset()
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
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.setTokens('test-access-token', 'test-refresh-token')
  auth.setUser({ id: 1, role: 'ADMIN' })
  globalThis.__testPinia = pinia
})

afterEach(() => {
  disposePinia(globalThis.__testPinia)
  delete globalThis.__testPinia
  vi.unstubAllGlobals()
})

describe('chat startConversation stale success', () => {
  it('returns the created conversation payload when the session rotates mid-flight', async () => {
    // 线上缺陷：access token 过期 → 401 → axios 拦截器刷新换新 token，
    // 会话其实已创建成功，但旧逻辑只回 { success: true, stale: true }，
    // 调用方对 undefined 取 id 抛 TypeError，按钮永久卡「打开中…」。
    const chat = useChatStore()
    const auth = useAuthStore()
    const conversation = { id: 42, target_user_id: 7, order_id: 9, unread_count: 0 }

    apiPost.mockImplementation(async () => {
      // 模拟拦截器在 POST 在飞期间刷新令牌，导致 auth context 翻转
      auth.setTokens('rotated-access-token', 'test-refresh-token')
      return { data: conversation }
    })

    const result = await chat.startConversation(7, 9)

    expect(result).toEqual({ success: true, stale: true, data: conversation })
    // 契约：success 为 true 时必须带 data，调用方才敢取 result.data.id
    expect(result.success === true ? result.data : true).toBeTruthy()
    // stale 路径不回写 store（context 已翻转，数据可能属于旧会话视角）
    expect(chat.conversations).toEqual([])
    expect(chat.unreadByConversation).toEqual({})
  })

  it('returns the conversation and writes it to the store on the normal path', async () => {
    const chat = useChatStore()
    const conversation = { id: 42, target_user_id: 7, order_id: 9, unread_count: 2 }

    apiPost.mockResolvedValue({ data: conversation })

    const result = await chat.startConversation(7, 9)

    expect(result.success).toBe(true)
    expect(result.stale).toBeUndefined()
    expect(result.data).toEqual(conversation)
    expect(chat.conversations.map((item) => item.id)).toEqual([42])
    expect(chat.unreadByConversation[42]).toBe(2)
  })
})
