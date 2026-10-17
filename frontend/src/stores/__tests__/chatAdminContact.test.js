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

const PRESET_MESSAGE = '订单 #9 的售后申请（¥50）我暂时联系不上发单人，请平台协助裁定结案。'
const conversation = { id: 42, order_id: 9, unread_count: 0 }

function mockAdminTarget(adminId = 7) {
  apiGet.mockResolvedValue({ data: { admin_id: adminId, admin_username: 'platform-admin' } })
}

// 与 chatConversationStale.test.js 同款的最小 DOM 桩：chat store 在模块加载期
// 就会挂 window 监听，WS 相关定时器也依赖 window。
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

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  stubBrowserGlobals()
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.setTokens('test-access-token', 'test-refresh-token')
  auth.setUser({ id: 1, role: 'BOOSTER' })
  globalThis.__testPinia = pinia
})

afterEach(() => {
  disposePinia(globalThis.__testPinia)
  delete globalThis.__testPinia
  vi.unstubAllGlobals()
})

describe('chat startAdminConversation', () => {
  it('chains admin-target → conversations → sendMessage when presetMessage is set', async () => {
    const chat = useChatStore()
    mockAdminTarget()
    apiPost.mockImplementation(async (url) => (
      url === '/chat/conversations'
        ? { data: conversation }
        : { data: { id: 100, conversation_id: 42, content: PRESET_MESSAGE } }
    ))

    const result = await chat.startAdminConversation({ presetMessage: PRESET_MESSAGE })

    expect(result.success).toBe(true)
    expect(result.data).toEqual(conversation)
    // 调用顺序：先取在线管理员，再直达会话，最后发预填消息
    expect(apiGet).toHaveBeenCalledTimes(1)
    expect(apiGet).toHaveBeenCalledWith('/chat/admin-target')
    expect(apiPost).toHaveBeenCalledTimes(2)
    expect(apiPost.mock.calls[0][0]).toBe('/chat/conversations')
    // 一律 PRIVATE 会话：管理员不是订单参与方，带 order_id 会撞参与者校验
    expect(apiPost.mock.calls[0][1]).toEqual({ target_user_id: 7, order_id: null })
    expect(apiPost.mock.calls[1][0]).toBe('/chat/conversations/42/messages')
    expect(apiPost.mock.calls[1][1]).toEqual({ content: PRESET_MESSAGE })
    // 会话写入 store，消息进入对应桶
    expect(chat.conversations.map((item) => item.id)).toEqual([42])
    expect(chat.messagesByConversation[42].map((item) => item.id)).toEqual([100])
  })

  it('skips sendMessage when presetMessage is empty', async () => {
    const chat = useChatStore()
    mockAdminTarget()
    apiPost.mockResolvedValue({ data: conversation })

    const result = await chat.startAdminConversation()

    expect(result.success).toBe(true)
    expect(result.data).toEqual(conversation)
    expect(apiGet).toHaveBeenCalledWith('/chat/admin-target')
    // 只有建会话一次请求，不发预填消息
    expect(apiPost).toHaveBeenCalledTimes(1)
    expect(apiPost.mock.calls[0][0]).toBe('/chat/conversations')
  })

  it('always creates a private conversation (order_id null) regardless of an order context', async () => {
    const chat = useChatStore()
    mockAdminTarget()
    apiPost.mockResolvedValue({ data: { id: 43, order_id: null, unread_count: 0 } })

    const result = await chat.startAdminConversation()

    expect(result.success).toBe(true)
    expect(apiPost.mock.calls[0][1]).toEqual({ target_user_id: 7, order_id: null })
  })

  it('unwraps admin-target failure into { success: false, error }', async () => {
    const chat = useChatStore()
    apiGet.mockRejectedValue({ message: '暂无在线的管理员' })

    const result = await chat.startAdminConversation({ presetMessage: PRESET_MESSAGE })

    expect(result).toEqual({ success: false, error: '暂无在线的管理员' })
    // 取管理员就失败：不再建会话、不发消息
    expect(apiPost).not.toHaveBeenCalled()
    expect(chat.error).toBe('暂无在线的管理员')
  })

  it('propagates conversation creation failure without sending the preset message', async () => {
    const chat = useChatStore()
    mockAdminTarget()
    apiPost.mockRejectedValue({ message: '网络异常' })

    const result = await chat.startAdminConversation({ orderId: 9, presetMessage: PRESET_MESSAGE })

    expect(result.success).toBe(false)
    expect(result.error).toBe('网络异常')
    expect(apiPost).toHaveBeenCalledTimes(1)
  })

  it('returns failure when no admin is online', async () => {
    const chat = useChatStore()
    apiGet.mockResolvedValue({ data: { admin_id: null, admin_username: null } })

    const result = await chat.startAdminConversation()

    expect(result.success).toBe(false)
    expect(result.error).toBe('暂无在线的管理员，请稍后重试')
    expect(apiPost).not.toHaveBeenCalled()
  })
})
