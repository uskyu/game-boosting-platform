import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import { useNotificationsStore } from '@/stores/notifications'
import { useAuthStore } from '@/stores/auth'
import { usePublishPermissionStore } from '@/stores/publishPermission'

// vitest 默认 node 环境没有 localStorage：auth store 的 token 初始化需要它
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
}

let pinia

function authenticate() {
  const auth = useAuthStore()
  auth.setTokens('access-token', 'refresh-token')
  auth.setUser({ id: 5, role: 'BOOSTER', can_publish: false, can_accept: true })
  return auth
}

function respondByUrl(url) {
  if (url === '/auth/me') {
    return Promise.resolve({ data: { id: 5, role: 'BOOSTER', can_publish: true, can_accept: true } })
  }
  if (url === '/publish-permission/mine') {
    return Promise.resolve({ data: { can_publish: true, apply_enabled: true } })
  }
  return Promise.resolve({ data: {} })
}

function reviewNotification(overrides = {}) {
  return {
    id: 201,
    type: 'APPLICATION_APPROVED',
    title: '发单权限申请已通过',
    content: '您的发单权限申请已通过，现在可以发布订单。',
    link: '/publish-permission',
    is_read: false,
    ...overrides,
  }
}

beforeEach(() => {
  apiGet.mockReset()
  storage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('审核结果通知刷新发单权限', () => {
  it('APPLICATION_APPROVED 且 link 精确匹配时刷新 auth 与 publishPermission', async () => {
    const auth = authenticate()
    apiGet.mockImplementation(respondByUrl)
    const notifications = useNotificationsStore()

    expect(notifications.handleRealtimeNotification(reviewNotification())).toBe(true)

    await vi.waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith('/auth/me')
      expect(apiGet).toHaveBeenCalledWith('/publish-permission/mine')
    })
    expect(auth.canPublish).toBe(true)
    expect(usePublishPermissionStore().mine.can_publish).toBe(true)
  })

  it('APPLICATION_REJECTED 同样会刷新，其他类型或非目标 link 不会', async () => {
    authenticate()
    apiGet.mockImplementation(respondByUrl)
    const notifications = useNotificationsStore()

    // 不匹配：系统公告、审核结果但 link 不是发单权限页
    notifications.handleRealtimeNotification(reviewNotification({ id: 101, type: 'SYSTEM_ANNOUNCEMENT' }))
    notifications.handleRealtimeNotification(reviewNotification({ id: 102, link: '/orders' }))
    await Promise.resolve()
    expect(apiGet).not.toHaveBeenCalled()

    notifications.handleRealtimeNotification(reviewNotification({
      id: 301,
      type: 'APPLICATION_REJECTED',
      title: '发单权限申请已驳回',
      content: '您的发单权限申请已驳回：资料不足',
    }))
    await vi.waitFor(() => {
      expect(apiGet).toHaveBeenCalledWith('/auth/me')
      expect(apiGet).toHaveBeenCalledWith('/publish-permission/mine')
    })
  })

  it('重复到达的同一条通知不重复触发刷新', async () => {
    authenticate()
    apiGet.mockImplementation(respondByUrl)
    const notifications = useNotificationsStore()
    const payload = reviewNotification()

    expect(notifications.handleRealtimeNotification(payload)).toBe(true)
    await vi.waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2))
    const requestCount = apiGet.mock.calls.length

    // 相同 id 再推一次：去重分支提前返回，不再打接口
    expect(notifications.handleRealtimeNotification(payload)).toBe(false)
    await Promise.resolve()
    expect(apiGet).toHaveBeenCalledTimes(requestCount)
  })
})
