import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
  },
}))

import { usePublishPermissionStore } from '@/stores/publishPermission'

let pinia

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

function makeLatest(overrides = {}) {
  return {
    id: 8,
    user_id: 3,
    username: 'boss',
    email: 'boss@example.com',
    user_created_at: '2026-10-01T00:00:00Z',
    reason: '长期派单',
    status: 'PENDING',
    decision_reason: null,
    reviewed_by: null,
    reviewed_by_name: null,
    created_at: '2026-11-01T00:00:00Z',
    resolved_at: null,
    history_count: 2,
    ...overrides,
  }
}

describe('publishPermission store', () => {
  it('fetchMine normalizes the mine payload', async () => {
    const store = usePublishPermissionStore()
    const latest = makeLatest()
    apiGet.mockResolvedValueOnce({
      data: { can_publish: false, apply_enabled: true, latest, has_pending: true },
    })

    const result = await store.fetchMine()

    expect(apiGet).toHaveBeenCalledTimes(1)
    expect(apiGet).toHaveBeenCalledWith('/publish-permission/mine')
    expect(result.success).toBe(true)
    expect(store.mine.can_publish).toBe(false)
    expect(store.mine.apply_enabled).toBe(true)
    expect(store.mine.latest).toEqual(latest)
    expect(store.mine.has_pending).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('fetchMine defaults apply_enabled to true and infers has_pending from latest', async () => {
    const store = usePublishPermissionStore()
    apiGet.mockResolvedValueOnce({
      data: { can_publish: true, latest: makeLatest({ status: 'PENDING' }) },
    })

    const result = await store.fetchMine()

    expect(result.success).toBe(true)
    expect(store.mine.apply_enabled).toBe(true)
    expect(store.mine.has_pending).toBe(true)
    expect(store.mine.latest.status).toBe('PENDING')
  })

  it('fetchMine surfaces the API error without clobbering results', async () => {
    const store = usePublishPermissionStore()
    apiGet.mockRejectedValueOnce({ message: '网络异常' })

    const result = await store.fetchMine()

    expect(result).toEqual({ success: false, error: '网络异常' })
    expect(store.error).toBe('网络异常')
    expect(store.loading).toBe(false)
  })

  it('apply posts the trimmed reason to the frozen endpoint', async () => {
    const store = usePublishPermissionStore()
    const created = makeLatest()
    apiPost.mockResolvedValueOnce({ data: created })

    const result = await store.apply('  长期派单  ')

    expect(apiPost).toHaveBeenCalledWith('/publish-permission/apply', { reason: '长期派单' })
    expect(result).toEqual({ success: true, data: created })
    expect(store.submitting).toBe(false)
  })

  it('apply omits an empty reason and reports backend errors', async () => {
    const store = usePublishPermissionStore()
    apiPost.mockResolvedValueOnce({ data: makeLatest() })
    await store.apply('   ')

    expect(apiPost).toHaveBeenCalledWith('/publish-permission/apply', {})

    apiPost.mockRejectedValueOnce({ message: '已有待审核申请' })
    const failure = await store.apply('再试一次')

    expect(failure).toEqual({ success: false, error: '已有待审核申请' })
    expect(store.error).toBe('已有待审核申请')
    expect(store.submitting).toBe(false)
  })

  it('withdraw posts to the request-specific endpoint', async () => {
    const store = usePublishPermissionStore()
    apiPost.mockResolvedValueOnce({ data: makeLatest({ status: 'WITHDRAWN' }) })

    const result = await store.withdraw(12)

    expect(apiPost).toHaveBeenCalledWith('/publish-permission/12/withdraw')
    expect(result.success).toBe(true)
    expect(store.submitting).toBe(false)
  })

  it('withdraw forwards the failure message', async () => {
    const store = usePublishPermissionStore()
    apiPost.mockRejectedValueOnce({ message: '申请不存在' })

    const result = await store.withdraw(12)

    expect(result).toEqual({ success: false, error: '申请不存在' })
    expect(store.submitting).toBe(false)
  })

  it('fetchAdminRequests sends status/q/page/page_size and stores pagination', async () => {
    const store = usePublishPermissionStore()
    apiGet.mockResolvedValueOnce({
      data: { items: [makeLatest()], total: 41, page: 2, page_size: 20, pages: 3 },
    })

    const result = await store.fetchAdminRequests({
      status: 'PENDING',
      q: '  boss  ',
      page: 2,
      pageSize: 20,
    })

    expect(apiGet).toHaveBeenCalledWith('/admin/publish-permission/requests', {
      params: { page: 2, page_size: 20, status: 'PENDING', q: 'boss' },
    })
    expect(result.success).toBe(true)
    expect(store.adminRequests).toHaveLength(1)
    expect(store.adminPagination).toEqual({ page: 2, pageSize: 20, total: 41, pages: 3 })
    expect(store.loading).toBe(false)
  })

  it('fetchAdminRequests omits empty status/q and defaults to the stored page', async () => {
    const store = usePublishPermissionStore()
    apiGet.mockResolvedValueOnce({
      data: { items: [], total: 0, page: 1, page_size: 20, pages: 0 },
    })

    const result = await store.fetchAdminRequests({ status: '', q: '   ' })

    expect(apiGet).toHaveBeenCalledWith('/admin/publish-permission/requests', {
      params: { page: 1, page_size: 20 },
    })
    expect(result.success).toBe(true)
    expect(store.adminPagination).toEqual({ page: 1, pageSize: 20, total: 0, pages: 0 })
    expect(store.loading).toBe(false)
  })

  it('reviewRequest posts action and reason to the review endpoint', async () => {
    const store = usePublishPermissionStore()
    const reviewed = makeLatest({ status: 'REJECTED', decision_reason: '暂不开放' })
    apiPost.mockResolvedValueOnce({ data: reviewed })

    const result = await store.reviewRequest(8, { action: 'reject', reason: '  暂不开放  ' })

    expect(apiPost).toHaveBeenCalledWith('/admin/publish-permission/requests/8/review', {
      action: 'reject',
      reason: '暂不开放',
    })
    expect(result).toEqual({ success: true, data: reviewed })
    expect(store.submitting).toBe(false)
  })

  it('reviewRequest approves without a reason and surfaces errors', async () => {
    const store = usePublishPermissionStore()
    apiPost.mockResolvedValueOnce({ data: makeLatest({ status: 'APPROVED' }) })
    await store.reviewRequest(8, { action: 'approve' })

    expect(apiPost).toHaveBeenCalledWith('/admin/publish-permission/requests/8/review', {
      action: 'approve',
    })

    apiPost.mockRejectedValueOnce({ message: '该申请已处理' })
    const failure = await store.reviewRequest(8, { action: 'approve' })

    expect(failure).toEqual({ success: false, error: '该申请已处理' })
    expect(store.error).toBe('该申请已处理')
    expect(store.submitting).toBe(false)
  })

  it('reset clears the previous account/admin residue before loading', async () => {
    const store = usePublishPermissionStore()
    // 先灌入上一账号/上一管理员的残留状态
    apiGet.mockResolvedValueOnce({
      data: { can_publish: false, apply_enabled: true, latest: makeLatest({ reason: '旧账号理由' }) },
    })
    await store.fetchMine()
    apiGet.mockResolvedValueOnce({
      data: { items: [makeLatest()], total: 41, page: 3, page_size: 20, pages: 3 },
    })
    await store.fetchAdminRequests({ page: 3 })
    store.error = '旧错误'
    store.submitting = true

    store.reset()

    expect(store.mine).toEqual({
      can_publish: false,
      apply_enabled: true,
      latest: null,
      has_pending: false,
    })
    expect(store.adminRequests).toEqual([])
    expect(store.adminPagination).toEqual({ page: 1, pageSize: 20, total: 0, pages: 0 })
    expect(store.loading).toBe(false)
    expect(store.submitting).toBe(false)
    expect(store.error).toBeNull()
  })
})
