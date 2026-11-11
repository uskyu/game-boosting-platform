import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiDelete = vi.fn()

vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    delete: (...args) => apiDelete(...args),
  },
}))

import {
  BLACKLIST_LOOKUP_NOT_FOUND,
  useBlacklistStore,
} from '@/stores/blacklist'

let pinia

function blacklistItem(id, userId, extra = {}) {
  return {
    id,
    blocked_user_id: userId,
    username: `user-${userId}`,
    avatar_url: null,
    reason: null,
    created_at: '2026-11-01T02:00:00Z',
    ...extra,
  }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiDelete.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('blacklist store：列表', () => {
  it('GET /blacklist/mine 携带分页参数并映射列表/分页，同时记住已拉黑 id', async () => {
    const store = useBlacklistStore()
    apiGet.mockResolvedValueOnce({
      data: {
        items: [blacklistItem(1, 7), blacklistItem(2, 9)],
        total: 2,
        page: 1,
        page_size: 20,
        pages: 1,
      },
    })

    const result = await store.fetchMine(1, 20)

    expect(apiGet).toHaveBeenCalledWith('/blacklist/mine', {
      params: { page: 1, page_size: 20 },
    })
    expect(result.success).toBe(true)
    expect(store.items).toHaveLength(2)
    expect(store.pagination).toEqual({ page: 1, pageSize: 20, total: 2, pages: 1 })
    expect(store.isBlocked(7)).toBe(true)
    expect(store.isBlocked(9)).toBe(true)
    expect(store.isBlocked(8)).toBe(false)
  })

  it('列表加载失败写入 error 并返回 status，不抛异常', async () => {
    const store = useBlacklistStore()
    apiGet.mockRejectedValueOnce({ message: '网络异常', status: 500 })

    const result = await store.fetchMine(1, 20)

    expect(result.success).toBe(false)
    expect(result.status).toBe(500)
    expect(store.error).toBe('网络异常')
    expect(store.loading).toBe(false)
  })
})

describe('blacklist store：精确查找', () => {
  it('只请求完全匹配的关键字，查不到（404）显示固定文案，不保留候选列表', async () => {
    const store = useBlacklistStore()
    apiGet.mockRejectedValueOnce({ message: '未找到该用户', status: 404 })

    const result = await store.lookup('someone')

    expect(apiGet).toHaveBeenCalledWith('/blacklist/lookup', { params: { q: 'someone' } })
    expect(result.success).toBe(false)
    expect(result.notFound).toBe(true)
    expect(store.lookupError).toBe(BLACKLIST_LOOKUP_NOT_FOUND)
    expect(store.lookupResult).toBeNull()
  })

  it('命中时只保存这一个用户，不返回/渲染任何候选列表', async () => {
    const store = useBlacklistStore()
    apiGet.mockResolvedValueOnce({ data: { id: 7, username: 'target', avatar_url: null } })

    const result = await store.lookup('  target  ')

    expect(apiGet).toHaveBeenCalledWith('/blacklist/lookup', { params: { q: 'target' } })
    expect(result.success).toBe(true)
    expect(store.lookupResult).toEqual({ id: 7, username: 'target', avatar_url: null })
  })

  it('少于 2 个字符直接拒绝，不打后端（防枚举）', async () => {
    const store = useBlacklistStore()

    const result = await store.lookup('a')

    expect(result.invalid).toBe(true)
    expect(apiGet).not.toHaveBeenCalled()
    expect(store.lookupError).toMatch(/至少 2 个字符/)
  })

  it('clearLookup 清空命中用户与错误', async () => {
    const store = useBlacklistStore()
    apiGet.mockResolvedValueOnce({ data: { id: 7, username: 'target' } })
    await store.lookup('target')

    store.clearLookup()

    expect(store.lookupResult).toBeNull()
    expect(store.lookupError).toBe('')
  })
})

describe('blacklist store：拉黑 / 解除', () => {
  it('POST /blacklist 带 user_id 与可选备注；成功后本地立即标记已拉黑', async () => {
    const store = useBlacklistStore()
    apiPost.mockResolvedValueOnce({ data: blacklistItem(1, 7, { reason: '多次沟通无果' }) })

    const result = await store.add(7, '  多次沟通无果  ')

    expect(apiPost).toHaveBeenCalledWith('/blacklist', {
      user_id: 7,
      reason: '多次沟通无果',
    })
    expect(result.success).toBe(true)
    expect(store.isBlocked(7)).toBe(true)
  })

  it('不填备注时不提交空 reason 字段', async () => {
    const store = useBlacklistStore()
    apiPost.mockResolvedValueOnce({ data: blacklistItem(1, 7) })

    await store.add(7, '   ')

    expect(apiPost).toHaveBeenCalledWith('/blacklist', { user_id: 7 })
  })

  it('重复拉黑后端幂等 200 时仍按成功处理（不重复报错）', async () => {
    const store = useBlacklistStore()
    apiPost.mockResolvedValue({ data: blacklistItem(1, 7) })

    const first = await store.add(7)
    const second = await store.add(7)

    expect(first.success).toBe(true)
    expect(second.success).toBe(true)
    expect(store.isBlocked(7)).toBe(true)
  })

  it('拉黑失败返回后端文案并复位 submitting', async () => {
    const store = useBlacklistStore()
    apiPost.mockRejectedValueOnce({ message: '该用户不可拉黑', status: 400 })

    const result = await store.add(7)

    expect(result.success).toBe(false)
    expect(result.error).toBe('该用户不可拉黑')
    expect(store.submitting).toBe(false)
  })

  it('DELETE /blacklist/{id} 成功后从列表和本地标记同时移除并递减 total', async () => {
    const store = useBlacklistStore()
    apiGet.mockResolvedValueOnce({
      data: {
        items: [blacklistItem(1, 7), blacklistItem(2, 9)],
        total: 2,
        page: 1,
        page_size: 20,
        pages: 1,
      },
    })
    await store.fetchMine(1, 20)
    apiDelete.mockResolvedValueOnce({ data: '' })

    const result = await store.remove(7)

    expect(apiDelete).toHaveBeenCalledWith('/blacklist/7')
    expect(result.success).toBe(true)
    expect(store.items.map((item) => item.blocked_user_id)).toEqual([9])
    expect(store.isBlocked(7)).toBe(false)
    expect(store.pagination.total).toBe(1)
  })

  it('解除失败保留列表并返回错误', async () => {
    const store = useBlacklistStore()
    apiGet.mockResolvedValueOnce({
      data: { items: [blacklistItem(1, 7)], total: 1, page: 1, page_size: 20, pages: 1 },
    })
    await store.fetchMine(1, 20)
    apiDelete.mockRejectedValueOnce({ message: '解除失败', status: 500 })

    const result = await store.remove(7)

    expect(result.success).toBe(false)
    expect(result.error).toBe('解除失败')
    expect(store.items).toHaveLength(1)
    expect(store.isBlocked(7)).toBe(true)
  })
})
