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

function conversationsPage(page, ids) {
  return {
    data: {
      items: ids.map((id) => ({
        id,
        last_message_at: new Date(2026, 10, 26, 12, 0, id % 60).toISOString(),
      })),
      total: 60,
      page,
      page_size: 20,
      pages: 3,
    },
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
  auth.setUser(owner)
  globalThis.__testPinia = pinia
})

afterEach(() => {
  disposePinia(globalThis.__testPinia)
  delete globalThis.__testPinia
  vi.unstubAllGlobals()
})

describe('chat conversation pagination', () => {
  it('loads page 1, appends later pages, and reports whether more remain', async () => {
    const chat = useChatStore()
    apiGet.mockResolvedValueOnce(conversationsPage(1, Array.from({ length: 20 }, (_, i) => i + 1)))

    const first = await chat.fetchConversations({ page: 1, pageSize: 20 })
    expect(first.success).toBe(true)
    expect(chat.conversations).toHaveLength(20)
    expect(chat.conversationsTotal).toBe(60)
    expect(chat.conversationsPage).toBe(1)
    expect(chat.conversationsPages).toBe(3)
    expect(chat.hasMoreConversations).toBe(true)

    apiGet.mockResolvedValueOnce(conversationsPage(2, Array.from({ length: 20 }, (_, i) => i + 21)))
    const second = await chat.loadMoreConversations()
    expect(second.success).toBe(true)
    expect(chat.conversations).toHaveLength(40)
    expect(chat.conversationsPage).toBe(2)
    expect(chat.hasMoreConversations).toBe(true)

    apiGet.mockResolvedValueOnce(conversationsPage(3, Array.from({ length: 20 }, (_, i) => i + 41)))
    await chat.loadMoreConversations()
    expect(chat.conversations).toHaveLength(60)
    expect(chat.conversationsPage).toBe(3)
    expect(chat.hasMoreConversations).toBe(false)
  })

  it('does not duplicate a conversation when a page overlaps', async () => {
    const chat = useChatStore()
    apiGet.mockResolvedValueOnce(conversationsPage(1, Array.from({ length: 20 }, (_, i) => i + 1)))
    await chat.fetchConversations({ page: 1, pageSize: 20 })

    // 新旧请求之间插入新会话，第二页与第一页重叠一条
    apiGet.mockResolvedValueOnce(conversationsPage(2, [20, ...Array.from({ length: 19 }, (_, i) => i + 21)]))
    await chat.loadMoreConversations()

    expect(chat.conversations).toHaveLength(39)
    expect(chat.conversations.filter((item) => item.id === 20)).toHaveLength(1)
  })

  it('does not request another page once the last page is loaded', async () => {
    const chat = useChatStore()
    apiGet.mockResolvedValueOnce(conversationsPage(1, Array.from({ length: 20 }, (_, i) => i + 1)))
    await chat.fetchConversations({ page: 1, pageSize: 20 })
    apiGet.mockResolvedValueOnce(conversationsPage(2, Array.from({ length: 20 }, (_, i) => i + 21)))
    await chat.loadMoreConversations()
    apiGet.mockResolvedValueOnce(conversationsPage(3, Array.from({ length: 20 }, (_, i) => i + 41)))
    await chat.loadMoreConversations()

    const callsBefore = apiGet.mock.calls.length
    const result = await chat.loadMoreConversations()
    expect(result).toEqual({ success: true, noMore: true })
    expect(apiGet.mock.calls.length).toBe(callsBefore)
  })
})
