import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

// 拦截器的行为靠这个模块里的 api.post 驱动；refreshAccessToken 要能断言
// "改密码期间绝不走刷新"。
const apiPost = vi.fn()
const refreshAccessToken = vi.fn()

vi.mock('@/utils/api', () => ({
  default: {
    get: vi.fn(),
    post: (...args) => apiPost(...args),
    put: vi.fn(),
  },
  refreshAccessToken: (...args) => refreshAccessToken(...args),
}))

import { useAuthStore } from '@/stores/auth'
import { pendingSessionRotation } from '@/utils/sessionRotation'

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
  apiPost.mockReset()
  refreshAccessToken.mockReset()
  storage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

function changePasswordResponse(overrides = {}) {
  return {
    data: {
      access_token: 'new-access',
      refresh_token: 'new-refresh',
      token_type: 'bearer',
      expires_in: 7200,
      user: { id: 1, email: 'u@example.com', username: 'U', role: 'BOOSTER' },
      ...overrides,
    },
  }
}

describe('改密码后本设备保持登录（令牌轮换）', () => {
  // 服务端在改密码时作废旧令牌，同一响应返回新令牌。前端必须把新令牌存下来，
  // 否则下一次请求立刻 401，用户"刚改完密码就被踢下线"。
  it('改密码成功后保存响应里的新令牌', async () => {
    const store = useAuthStore()
    store.setTokens('old-access', 'old-refresh')
    apiPost.mockResolvedValueOnce(changePasswordResponse())

    const result = await store.changePassword('OldPass123', 'NewPass123')

    expect(result).toEqual({ success: true })
    expect(store.accessToken).toBe('new-access')
    expect(store.refreshToken).toBe('new-refresh')
    expect(localStorage.getItem('access_token')).toBe('new-access')
    expect(localStorage.getItem('refresh_token')).toBe('new-refresh')
  })

  it('改密码成功后同步刷新用户信息（角色/昵称可能随响应变化）', async () => {
    const store = useAuthStore()
    store.setTokens('old-access', 'old-refresh')
    apiPost.mockResolvedValueOnce(changePasswordResponse({
      user: { id: 1, email: 'u@example.com', username: '改名后', role: 'ADMIN' },
    }))

    await store.changePassword('OldPass123', 'NewPass123')

    expect(store.user.username).toBe('改名后')
  })

  it('改密码请求必须跳过轮换闸门（否则自己等自己，永久挂起）', async () => {
    const store = useAuthStore()
    store.setTokens('old-access', 'old-refresh')
    apiPost.mockResolvedValueOnce(changePasswordResponse())

    await store.changePassword('OldPass123', 'NewPass123')

    const config = apiPost.mock.calls[0][2]
    expect(config).toMatchObject({ skipSessionRotationWait: true })
  })

  it('改密码期间闸门打开，请求结束后关闭', async () => {
    const store = useAuthStore()
    store.setTokens('old-access', 'old-refresh')
    const pending = deferred()
    apiPost.mockReturnValueOnce(pending.promise)

    const inflight = store.changePassword('OldPass123', 'NewPass123')
    expect(pendingSessionRotation()).not.toBeNull()

    pending.resolve(changePasswordResponse())
    await inflight
    expect(pendingSessionRotation()).toBeNull()
  })

  it('改密码失败时也关闭闸门，且不动现有令牌', async () => {
    const store = useAuthStore()
    store.setTokens('old-access', 'old-refresh')
    apiPost.mockRejectedValueOnce({ status: 400, message: '当前密码错误' })

    const result = await store.changePassword('Wrong', 'NewPass123')

    expect(result).toEqual({ success: false, error: '当前密码错误' })
    expect(store.accessToken).toBe('old-access')
    expect(store.refreshToken).toBe('old-refresh')
    // 闸门必须关掉：否则后续所有 401 都会永久卡在 await 上
    expect(pendingSessionRotation()).toBeNull()
  })

  it('改密码失败不重置 loading（loading 复位由 finally 负责）', async () => {
    const store = useAuthStore()
    store.setTokens('old-access', 'old-refresh')
    apiPost.mockRejectedValueOnce({ status: 400, message: '当前密码错误' })

    await store.changePassword('Wrong', 'NewPass123')
    expect(store.loading).toBe(false)
  })
})

describe('被服务端踢下线时的状态', () => {
  it('logout 记录退出原因，登录页才能解释为什么回到这里', () => {
    const store = useAuthStore()
    store.setTokens('a', 'r')

    store.logout('session-expired')

    expect(store.lastLogoutReason).toBe('session-expired')
    expect(store.accessToken).toBeNull()
  })

  it('主动登出不带原因，避免误显示"登录状态已失效"', () => {
    const store = useAuthStore()
    store.setTokens('a', 'r')

    store.logout()

    expect(store.lastLogoutReason).toBeNull()
  })

  it('重新登录后可清除失效提示', async () => {
    const store = useAuthStore()
    store.logout('session-expired')
    expect(store.lastLogoutReason).toBe('session-expired')

    store.clearLogoutReason()
    expect(store.lastLogoutReason).toBeNull()
  })

  it('logout 会清掉残留的轮换闸门（否则登出后 401 会卡死）', () => {
    const store = useAuthStore()
    store.setTokens('a', 'r')
    apiPost.mockReturnValueOnce(new Promise(() => {}))

    store.changePassword('Old', 'New') // 不 await：模拟在飞时被登出
    expect(pendingSessionRotation()).not.toBeNull()

    store.logout()

    expect(pendingSessionRotation()).toBeNull()
  })
})
