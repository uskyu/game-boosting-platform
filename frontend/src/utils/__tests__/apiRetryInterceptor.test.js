import { describe, expect, it, vi } from 'vitest'

// api.js 会拉入 auth store 与 router，这里按现有测试的约定把它们 mock 掉，
// 只验证响应拦截器对"网络层失败"的重试接线是否真的生效。
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    accessToken: 'test-token',
    refreshToken: 'test-refresh',
    setTokens: vi.fn(),
    logout: vi.fn(),
  }),
}))
vi.mock('@/router', () => ({
  default: {
    push: vi.fn(),
    currentRoute: { value: { fullPath: '/' } },
  },
}))

import api from '../api'

function networkFailure(config) {
  const err = new Error('timeout of 10000ms exceeded')
  err.config = config
  err.code = 'ECONNABORTED'
  return err
}

function serverFailure(config) {
  const err = new Error('Request failed with status code 500')
  err.config = config
  err.response = { status: 500, data: { detail: 'boom' }, headers: {}, config }
  return err
}

describe('api interceptor: network-level auto retry', () => {
  it('retries a GET once after a network-level failure', async () => {
    let attempts = 0
    api.defaults.adapter = async (config) => {
      attempts += 1
      if (attempts === 1) throw networkFailure(config)
      return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
    }
    const res = await api.get('/orders/')
    expect(res.data.ok).toBe(true)
    expect(attempts).toBe(2)
  }, 10000)

  it('gives up after one retry and surfaces a Chinese network message', async () => {
    let attempts = 0
    api.defaults.adapter = async (config) => {
      attempts += 1
      throw networkFailure(config)
    }
    await expect(api.get('/orders/')).rejects.toMatchObject({
      message: '网络连接超时，请检查网络后重试',
    })
    expect(attempts).toBe(2)
  }, 10000)

  it('never retries a POST so failures cannot duplicate writes', async () => {
    let attempts = 0
    api.defaults.adapter = async (config) => {
      attempts += 1
      throw networkFailure(config)
    }
    await expect(api.post('/orders/create', { title: 'x' })).rejects.toMatchObject({
      message: '网络连接超时，请检查网络后重试',
    })
    expect(attempts).toBe(1)
  }, 10000)

  it('does not retry when the server answered (5xx is not a black hole)', async () => {
    let attempts = 0
    api.defaults.adapter = async (config) => {
      attempts += 1
      throw serverFailure(config)
    }
    await expect(api.get('/orders/')).rejects.toMatchObject({ status: 500 })
    expect(attempts).toBe(1)
  })
})
