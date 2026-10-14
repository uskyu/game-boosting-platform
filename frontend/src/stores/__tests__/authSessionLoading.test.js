import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const refreshAccessToken = vi.fn()

vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: vi.fn(),
    put: vi.fn(),
  },
  refreshAccessToken: (...args) => refreshAccessToken(...args),
}))

import { useAuthStore } from '@/stores/auth'

// vitest 环境是 node：给 auth store 一个最小的 localStorage 垫片
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
}

let pinia

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  apiGet.mockReset()
  refreshAccessToken.mockReset()
  storage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('注销会复位 loading（登录按钮不得永久禁用）', () => {
  // 线上症状：冷启动 + 会话过期（/auth/me 401 → 刷新令牌失败）时，
  // 拦截器调 logout() 作废在飞的 fetchCurrentUser，其 finally 因序号
  // 不匹配跳过复位，loading 永久为 true → 登录页按钮永远"进入中..."、
  // 点了没反应，只有整页刷新才能恢复。
  it('在飞 /auth/me 被 logout 作废后，loading 复位且结果标记 stale', async () => {
    const store = useAuthStore()
    const pending = deferred()
    apiGet.mockImplementationOnce(() => pending.promise)
    store.setTokens('access-1', 'refresh-1')

    const inflight = store.fetchCurrentUser()
    expect(store.loading).toBe(true)

    // 401 拦截器刷新失败后的清理路径
    store.logout()
    expect(store.loading).toBe(false)
    expect(store.accessToken).toBeNull()
    expect(store.user).toBeNull()

    pending.reject({ status: 401, message: '未认证' })
    const result = await inflight

    expect(result).toEqual({ success: false, stale: true, error: '未认证' })
    expect(store.loading).toBe(false)
  })

  it('logout 无条件把 loading 归零（旧 Bug：残留 true 禁用登录按钮）', () => {
    const store = useAuthStore()
    store.loading = true

    store.logout()

    expect(store.loading).toBe(false)
  })

  it('注销后可以重新登录并把 loading 正常走完', async () => {
    const store = useAuthStore()
    store.loadout?.()
    store.logout()
    expect(store.loading).toBe(false)
  })
})
