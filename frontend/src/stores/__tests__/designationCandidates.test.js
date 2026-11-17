import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    put: vi.fn(),
    post: vi.fn(),
  },
}))

import { useOrdersStore } from '@/stores/orders'

let pinia

beforeEach(() => {
  apiGet.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('orders store：指定接单候选', () => {
  it('q 为空：只请求 recent，不额外传 params', async () => {
    const store = useOrdersStore()
    const payload = {
      query: '',
      recent: [
        { id: 7, username: 'recent-user', avatar_url: null, deposit_balance: 120, can_accept: true, selected_times: 3 },
      ],
      results: [],
    }
    apiGet.mockResolvedValueOnce({ data: payload })

    const result = await store.fetchDesignationCandidates()

    expect(apiGet).toHaveBeenCalledTimes(1)
    expect(apiGet).toHaveBeenCalledWith('/orders/designation-candidates')
    expect(result).toEqual({ success: true, data: payload })
    // 只读查询：不写共享 loading/error，也不碰列表缓存与分页
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.orders).toEqual([])
  })

  it('q 非空：trim 后作为 params.q 传给搜索结果接口', async () => {
    const store = useOrdersStore()
    const payload = {
      query: 'nick',
      recent: [],
      results: [
        { id: 9, username: 'nick', avatar_url: null, deposit_balance: 50, can_accept: false, selected_times: 0 },
      ],
    }
    apiGet.mockResolvedValueOnce({ data: payload })

    const result = await store.fetchDesignationCandidates('  nick  ')

    expect(apiGet).toHaveBeenCalledWith('/orders/designation-candidates', {
      params: { q: 'nick' },
    })
    expect(result.success).toBe(true)
    expect(result.data.results).toHaveLength(1)
    expect(result.data.results[0].username).toBe('nick')
  })

  it('失败时返回 { success: false, error }，不抛异常', async () => {
    const store = useOrdersStore()
    apiGet.mockRejectedValueOnce({ message: '搜索失败，请稍后重试', status: 500 })

    const result = await store.fetchDesignationCandidates('ab')

    expect(result).toEqual({ success: false, error: '搜索失败，请稍后重试' })
    expect(store.error).toBeNull()
    expect(store.loading).toBe(false)
  })
})
