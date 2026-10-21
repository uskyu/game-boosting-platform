import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 只验证响应拦截器在 401 时的分支选择，因此把 store/router 换成可控替身。
const storeState = {
  accessToken: 'old-access',
  refreshToken: 'old-refresh',
  // 真实 store 的 setTokens 会同时更新响应式状态和 localStorage；这里必须
  // 让它真的生效，否则"刷新成功后用新令牌重放"这一步会拿着旧令牌重试。
  setTokens: vi.fn((access, refresh) => {
    storeState.accessToken = access
    storeState.refreshToken = refresh
  }),
  logout: vi.fn(),
}

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => storeState,
}))
vi.mock('@/router', () => ({
  default: {
    push: vi.fn(),
    currentRoute: { value: { fullPath: '/profile' } },
  },
}))

import api from '../api'
import { beginSessionRotation, resetSessionRotation } from '@/utils/sessionRotation'

function unauthorized(config) {
  const err = new Error('Request failed with status code 401')
  err.config = config
  err.response = {
    status: 401,
    data: { detail: '登录状态已失效，请重新登录' },
    headers: {},
    config,
  }
  return err
}

function ok(config, data) {
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

// 刷新令牌走的是裸 axios.post（不是 api 实例），所以这里控制全局实例的
// adapter：resolve 时返回一对新令牌，reject 时模拟"刷新令牌也失效了"。
let refreshShouldFail = false
let refreshCalls = 0

beforeEach(() => {
  storeState.accessToken = 'old-access'
  storeState.refreshToken = 'old-refresh'
  storeState.setTokens.mockClear()
  storeState.logout.mockReset()
  refreshShouldFail = false
  refreshCalls = 0
  resetSessionRotation()

  axios.defaults.adapter = async (config) => {
    refreshCalls += 1
    if (refreshShouldFail) {
      const err = new Error('Request failed with status code 401')
      err.config = config
      err.response = { status: 401, data: { detail: '登录状态已失效' }, headers: {}, config }
      throw err
    }
    return ok(config, { access_token: 'refreshed-access', refresh_token: 'refreshed-refresh' })
  }
})

describe('api interceptor: 改密码期间的 401 不能把自己踢下线', () => {
  // 时序（线上会真实发生）：
  //   1. 用户在设置页点"修改密码"
  //   2. 服务端版本号 +1，旧令牌即刻作废，响应里带回新令牌
  //   3. 与此同时大厅/通知轮询带着旧令牌发出，回来是 401
  //   4. 若不拦截，401 会走刷新 → 旧 refresh 已被作废 → 刷新失败 → 强制登出
  // 结果：用户刚改完密码，自己反而被踢下线。这条测试锁住这个坑。
  it('轮换进行中遇到 401：等闸门放行后用新令牌重放，不触发刷新', async () => {
    const endRotation = beginSessionRotation()
    refreshShouldFail = true // 旧 refresh 已被服务端作废

    api.defaults.adapter = async (config) => {
      if (config.headers.Authorization === 'Bearer old-access') throw unauthorized(config)
      // 重放时必须带的是轮换后的新令牌
      expect(config.headers.Authorization).toBe('Bearer new-access')
      return ok(config, { ok: true })
    }

    const inflight = api.get('/orders/hall-digest')
    // 轮换完成：新令牌已落盘（changePassword 里 setTokens 的效果）
    storeState.accessToken = 'new-access'
    endRotation()

    const res = await inflight
    expect(res.data.ok).toBe(true)
    expect(refreshCalls).toBe(0)
    expect(storeState.logout).not.toHaveBeenCalled()
  }, 10000)

  it('没有轮换进行中时，401 仍走原有刷新路径（行为未被改坏）', async () => {
    // 拦截器里 `api(originalRequest)` 重放用的是**调用那一刻** store 里的
    // 令牌，所以这里靠 setTokens 的真实副作用（上面已实现）让重放带新令牌。
    api.defaults.adapter = async (config) => {
      if (config.headers.Authorization === 'Bearer old-access') throw unauthorized(config)
      expect(config.headers.Authorization).toBe('Bearer refreshed-access')
      return ok(config, { ok: true })
    }

    const res = await api.get('/orders/hall-digest')
    expect(res.data.ok).toBe(true)
    expect(refreshCalls).toBe(1)
    expect(storeState.setTokens).toHaveBeenCalledWith('refreshed-access', 'refreshed-refresh')
    expect(storeState.logout).not.toHaveBeenCalled()
  }, 10000)

  it('刷新失败时登出并标记 session-expired（登录页要能解释原因）', async () => {
    refreshShouldFail = true
    api.defaults.adapter = async (config) => {
      throw unauthorized(config)
    }

    await expect(api.get('/orders/hall-digest')).rejects.toBeTruthy()

    expect(storeState.logout).toHaveBeenCalledWith('session-expired')
  }, 10000)

  it('改密码请求自身带 skipSessionRotationWait，不等待闸门（否则自己等自己）', async () => {
    // 闸门故意不关闭：若拦截器错误地让改密码请求等闸门，这条会超时失败
    beginSessionRotation()
    let attempts = 0
    api.defaults.adapter = async (config) => {
      attempts += 1
      if (attempts === 1) throw unauthorized(config)
      return ok(config, { ok: true })
    }

    const res = await api.post(
      '/auth/change-password',
      { current_password: 'x', new_password: 'y' },
      { skipSessionRotationWait: true },
    )

    expect(res.data.ok).toBe(true)
    expect(attempts).toBe(2)
    // 走的是刷新分支，而不是卡在闸门上
    expect(refreshCalls).toBe(1)
  }, 10000)
})
