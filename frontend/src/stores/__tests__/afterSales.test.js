import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
  },
}))

import { useAfterSalesStore, parseInsufficientFundsError } from '@/stores/afterSales'

let pinia

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

function makeAfterSale(id, status = 'PENDING') {
  return {
    id,
    order_id: 12,
    amount: 30,
    reason: '未按约定完成',
    status,
    images: [{ url: '/uploads/after-sales/a.jpg', name: 'a.jpg' }],
    created_at: '2026-10-20T02:00:00Z',
  }
}

describe('afterSales store', () => {
  it('createAfterSale posts reason/amount/images and returns the created record', async () => {
    const store = useAfterSalesStore()
    const created = makeAfterSale(1)
    apiPost.mockResolvedValueOnce({ data: created })

    const result = await store.createAfterSale(12, {
      reason: '未按约定完成',
      amount: 30,
      images: ['/uploads/after-sales/a.jpg'],
    })

    expect(apiPost).toHaveBeenCalledWith('/orders/12/after-sales', {
      reason: '未按约定完成',
      amount: 30,
      images: ['/uploads/after-sales/a.jpg'],
    })
    expect(result).toEqual({ success: true, data: created })
    expect(store.submitting).toBe(false)
  })

  it('createAfterSale surfaces the backend Chinese detail on failure', async () => {
    const store = useAfterSalesStore()
    // api 拦截器已把 {detail} 解包成 err.message（见 utils/api.js readableApiError）
    apiPost.mockRejectedValueOnce({ message: '售后金额不能超过订单派单价 ¥50.00' })

    const result = await store.createAfterSale(12, { reason: '测试售后原因', amount: 999, images: [] })

    expect(result.success).toBe(false)
    expect(result.error).toBe('售后金额不能超过订单派单价 ¥50.00')
    expect(store.error).toBe('售后金额不能超过订单派单价 ¥50.00')
    expect(store.submitting).toBe(false)
  })

  it('fetchMyAfterSales sends role and status as query params', async () => {
    const store = useAfterSalesStore()
    apiGet.mockResolvedValueOnce({ data: [makeAfterSale(1)] })

    const result = await store.fetchMyAfterSales({ role: 'booster', status: 'pending' })

    expect(apiGet).toHaveBeenCalledWith('/after-sales/mine', {
      params: { role: 'booster', status: 'pending' },
    })
    expect(result.success).toBe(true)
    expect(store.myAfterSales).toHaveLength(1)
    expect(store.myAfterSalesLoaded).toBe(true)
    expect(store.hasPendingBoosterAfterSale).toBe(true)
    expect(store.myAfterSalesLoading).toBe(false)
  })

  it('fetchMyAfterSales without status omits the param and does not report a pending blocker', async () => {
    const store = useAfterSalesStore()
    apiGet.mockResolvedValueOnce({ data: { items: [makeAfterSale(2, 'REJECTED')] } })

    await store.fetchMyAfterSales({ role: 'requester' })

    expect(apiGet).toHaveBeenCalledWith('/after-sales/mine', { params: { role: 'requester' } })
    expect(store.myAfterSales).toHaveLength(1)
    expect(store.hasPendingBoosterAfterSale).toBe(false)
  })

  it('uploadAfterSaleImage posts multipart with the image field and a 120s timeout', async () => {
    const store = useAfterSalesStore()
    const uploaded = { url: '/uploads/after-sales/b.jpg', name: 'b.jpg', size: 1234, content_type: 'image/jpeg' }
    apiPost.mockResolvedValueOnce({ data: uploaded })

    const file = new File([new Uint8Array([1, 2, 3])], 'b.jpg', { type: 'image/jpeg' })
    const result = await store.uploadAfterSaleImage(12, file)

    expect(apiPost).toHaveBeenCalledTimes(1)
    const [path, body, config] = apiPost.mock.calls[0]
    expect(path).toBe('/orders/12/after-sale-images')
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('image')).toBe(file)
    expect(config).toMatchObject({
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    expect(result).toEqual({ success: true, data: uploaded })
  })

  it('uploadAfterSaleImage returns the unwrapped error message on failure', async () => {
    const store = useAfterSalesStore()
    apiPost.mockRejectedValueOnce({ message: '仅支持 JPG / PNG 格式' })

    const file = new File([new Uint8Array([1])], 'c.gif', { type: 'image/gif' })
    const result = await store.uploadAfterSaleImage(12, file)

    expect(result).toEqual({ success: false, error: '仅支持 JPG / PNG 格式', status: undefined })
  })

  it('fetchOrderAfterSales accepts both list and {items} payloads', async () => {
    const store = useAfterSalesStore()
    apiGet.mockResolvedValueOnce({ data: [makeAfterSale(1)] })
    await expect(store.fetchOrderAfterSales(12)).resolves.toMatchObject({ success: true })
    expect(apiGet).toHaveBeenCalledWith('/orders/12/after-sales')

    apiGet.mockResolvedValueOnce({ data: { items: [makeAfterSale(2)] } })
    const result = await store.fetchOrderAfterSales(12)
    expect(result.data).toHaveLength(1)
  })

  it('fetchAdminAfterSales maps filters to query params and builds pagination', async () => {
    const store = useAfterSalesStore()
    apiGet.mockResolvedValueOnce({
      data: { items: [makeAfterSale(1)], total: 21, page: 2, page_size: 20, pages: 2 },
    })

    const result = await store.fetchAdminAfterSales({ status: 'PENDING', orderId: 12, q: '售后', page: 2 })

    expect(apiGet).toHaveBeenCalledWith('/admin/after-sales', {
      params: { page: 2, page_size: 20, status: 'PENDING', order_id: 12, q: '售后' },
    })
    expect(result.success).toBe(true)
    expect(store.adminAfterSales).toHaveLength(1)
    expect(store.adminAfterSalesPagination).toEqual({ page: 2, pageSize: 20, total: 21, pages: 2 })
    expect(store.adminAfterSalesLoading).toBe(false)
  })

  it('reviewAfterSale posts the action with reason and unwraps failures', async () => {
    const store = useAfterSalesStore()
    apiPost.mockResolvedValueOnce({ data: makeAfterSale(1, 'REJECTED') })

    const ok = await store.reviewAfterSale(1, { action: 'reject', reason: '证据不足' })
    expect(apiPost).toHaveBeenCalledWith('/admin/after-sales/1/review', {
      action: 'reject',
      reason: '证据不足',
    })
    expect(ok.success).toBe(true)

    apiPost.mockRejectedValueOnce({ message: '售后单已处理，请勿重复操作' })
    const failed = await store.reviewAfterSale(1, { action: 'approve' })
    expect(failed).toEqual({ success: false, error: '售后单已处理，请勿重复操作' })
  })

  it('reviewAfterSale sends the adjusted amount only for approve', async () => {
    const store = useAfterSalesStore()
    apiPost.mockResolvedValueOnce({ data: makeAfterSale(1, 'APPROVED') })

    await store.reviewAfterSale(1, { action: 'approve', reason: '各让一步', amount: 20 })
    expect(apiPost).toHaveBeenCalledWith('/admin/after-sales/1/review', {
      action: 'approve',
      reason: '各让一步',
      amount: 20,
    })

    // close / reject 不传 amount；approve 未裁额时也不传（走后端默认全额）
    apiPost.mockResolvedValueOnce({ data: makeAfterSale(2, 'CLOSED') })
    await store.reviewAfterSale(2, { action: 'close', reason: '老板撤诉' })
    expect(apiPost).toHaveBeenLastCalledWith('/admin/after-sales/2/review', {
      action: 'close',
      reason: '老板撤诉',
    })

    apiPost.mockResolvedValueOnce({ data: makeAfterSale(3, 'APPROVED') })
    await store.reviewAfterSale(3, { action: 'approve' })
    expect(apiPost).toHaveBeenLastCalledWith('/admin/after-sales/3/review', { action: 'approve' })
  })

  it('withdrawAfterSale posts to the withdraw endpoint and unwraps failures', async () => {
    const store = useAfterSalesStore()
    apiPost.mockResolvedValueOnce({ data: makeAfterSale(1, 'CLOSED') })

    const ok = await store.withdrawAfterSale(12, 1)
    expect(apiPost).toHaveBeenCalledWith('/orders/12/after-sales/1/withdraw')
    expect(ok.success).toBe(true)
    expect(store.submitting).toBe(false)

    apiPost.mockRejectedValueOnce({ message: '该售后申请已处理，无法撤回' })
    const failed = await store.withdrawAfterSale(12, 1)
    expect(failed).toEqual({ success: false, error: '该售后申请已处理，无法撤回' })
  })

  it('acceptAfterSale posts to the accept endpoint and returns the settled record', async () => {
    const store = useAfterSalesStore()
    apiPost.mockResolvedValueOnce({ data: makeAfterSale(1, 'APPROVED') })

    const result = await store.acceptAfterSale(12, 1)

    expect(apiPost).toHaveBeenCalledWith('/orders/12/after-sales/1/accept')
    expect(result).toEqual({ success: true, data: makeAfterSale(1, 'APPROVED') })
    expect(store.submitting).toBe(false)
  })

  it('acceptAfterSale surfaces the backend Chinese detail on failure', async () => {
    const store = useAfterSalesStore()
    // api 拦截器已把 {detail} 解包成 err.message（见 utils/api.js readableApiError）
    apiPost.mockRejectedValueOnce({ message: '余额不足，无法扣划售后款项' })

    const result = await store.acceptAfterSale(12, 1)

    expect(result.success).toBe(false)
    expect(result.error).toBe('余额不足，无法扣划售后款项')
    expect(store.error).toBe('余额不足，无法扣划售后款项')
    expect(store.submitting).toBe(false)
  })

  it('acceptAfterSale exposes the structured shortfall when funds are insufficient', async () => {
    const store = useAfterSalesStore()
    // 后端可扣款不足时返回结构化 detail（after_sale_insufficient_funds），
    // api 拦截器把 message 提成 err.message、完整响应留在 err.original。
    const detail = {
      code: 'after_sale_insufficient_funds',
      message: '可用余额 ¥4.60 + 保证金 ¥10.00 不足以覆盖本次扣款 ¥20.00，尚缺 ¥5.40；请先充值或缴纳保证金后再同意退款',
      required: '20.00',
      available: '4.60',
      deposit: '10.00',
      shortfall: '5.40',
    }
    apiPost.mockRejectedValueOnce({
      status: 400,
      message: detail.message,
      original: { response: { data: { detail } } },
    })

    const result = await store.acceptAfterSale(12, 1)

    expect(result.success).toBe(false)
    expect(result.insufficient).toEqual({
      message: detail.message,
      required: '20.00',
      available: '4.60',
      deposit: '10.00',
      shortfall: '5.40',
    })
    expect(store.submitting).toBe(false)
  })

  it('acceptAfterSale omits the shortfall field for unrelated errors', async () => {
    const store = useAfterSalesStore()
    apiPost.mockRejectedValueOnce({
      status: 409,
      message: '该售后申请已处理',
      original: { response: { data: { detail: '该售后申请已处理' } } },
    })

    const result = await store.acceptAfterSale(12, 1)

    expect(result).toEqual({ success: false, error: '该售后申请已处理' })
    expect('insufficient' in result).toBe(false)
  })

  it('parseInsufficientFundsError ignores non-structured details', () => {
    expect(parseInsufficientFundsError(undefined)).toBeNull()
    expect(parseInsufficientFundsError({ message: '普通字符串 detail' })).toBeNull()
    expect(
      parseInsufficientFundsError({
        original: { response: { data: { detail: { code: 'other_code', message: 'x' } } } },
      })
    ).toBeNull()
    expect(
      parseInsufficientFundsError({
        original: { response: { data: { detail: { code: 'after_sale_insufficient_funds' } } } },
      })
    ).toMatchObject({ required: '', shortfall: '' })
  })
})
