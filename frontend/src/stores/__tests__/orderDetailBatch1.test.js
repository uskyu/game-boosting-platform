import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    put: (...args) => apiPut(...args),
  },
}))

import { useOrdersStore } from '@/stores/orders'

let pinia

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('decideCancelRequest 对 response.data 的空值兜底', () => {
  // 回归：后端偶发返回空体时，旧实现会把 null splice 进协商列表，
  // 详情页渲染 request.status 直接崩。
  it('response.data 为 null 时不改动 cancelRequests 列表', async () => {
    const store = useOrdersStore()
    store.cancelRequests = [{ id: 7, status: 'PENDING' }]
    apiPost.mockResolvedValue({ data: null })

    const result = await store.decideCancelRequest(42, 7, { action: 'approve' })

    expect(result).toEqual({ success: true, data: null })
    expect(store.cancelRequests).toEqual([{ id: 7, status: 'PENDING' }])
  })

  it('response.data 为 undefined 时也不插入 null 项', async () => {
    const store = useOrdersStore()
    store.cancelRequests = [{ id: 7, status: 'PENDING' }]
    apiPost.mockResolvedValue({})

    const result = await store.decideCancelRequest(42, 7, { action: 'approve' })

    expect(result.success).toBe(true)
    expect(store.cancelRequests).toEqual([{ id: 7, status: 'PENDING' }])
  })

  it('正常响应仍按 id 替换对应协商项', async () => {
    const store = useOrdersStore()
    store.cancelRequests = [
      { id: 7, status: 'PENDING' },
      { id: 8, status: 'PENDING' },
    ]
    apiPost.mockResolvedValue({ data: { id: 7, status: 'APPROVED' } })

    const result = await store.decideCancelRequest(42, 7, { action: 'approve' })

    expect(result).toEqual({ success: true, data: { id: 7, status: 'APPROVED' } })
    expect(store.cancelRequests).toEqual([
      { id: 7, status: 'APPROVED' },
      { id: 8, status: 'PENDING' },
    ])
    expect(apiPost).toHaveBeenCalledWith(
      '/orders/42/cancel-requests/7/decision',
      { action: 'approve' },
    )
  })

  it('列表里没有该 requestId 时不插入新项', async () => {
    const store = useOrdersStore()
    store.cancelRequests = [{ id: 8, status: 'PENDING' }]
    apiPost.mockResolvedValue({ data: { id: 7, status: 'APPROVED' } })

    await store.decideCancelRequest(42, 7, { action: 'approve' })

    expect(store.cancelRequests).toEqual([{ id: 8, status: 'PENDING' }])
  })

  it('请求失败时返回错误且不动列表', async () => {
    const store = useOrdersStore()
    store.cancelRequests = [{ id: 7, status: 'PENDING' }]
    apiPost.mockRejectedValue({ message: '仅收件方可操作' })

    const result = await store.decideCancelRequest(42, 7, { action: 'approve' })

    expect(result).toEqual({ success: false, error: '仅收件方可操作' })
    expect(store.cancelRequests).toEqual([{ id: 7, status: 'PENDING' }])
    expect(store.error).toBe('仅收件方可操作')
  })
})
