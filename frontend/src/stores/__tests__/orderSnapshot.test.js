import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
vi.mock('@/utils/api', () => ({
  default: { get: (...args) => apiGet(...args) },
}))

import { useOrdersStore } from '@/stores/orders'

let pinia

function orderListResponse(items) {
  return {
    data: {
      items,
      page: 1,
      page_size: 20,
      total: items.length,
      pages: 1,
    },
  }
}

function makeOrder({ cancelPending = false, claimCancelPending = false } = {}) {
  return {
    id: 42,
    status: 'LOCKED',
    claim_status: 'CLAIMED',
    claimed_count: 1,
    updated_at: '2026-10-08T12:00:00Z',
    deadline: '2026-10-09T12:00:00Z',
    priority: 1,
    accept_available_at: null,
    my_claim: { status: 'CLAIMED', cancel_pending: claimCancelPending },
    cancel_pending: cancelPending,
    compensation_amount: 25,
    title: 'Test order',
    intro: 'Test order details',
  }
}

beforeEach(() => {
  apiGet.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('orders store snapshot comparison', () => {
  it.each([
    ['order-level cancel_pending false → true', false, false, true, false],
    ['order-level cancel_pending true → false', true, false, false, false],
    ['claim-level cancel_pending false → true', false, false, false, true],
    ['claim-level cancel_pending true → false', false, true, false, false],
  ])('refreshes when %s changes while other fields stay the same', async (_label, initialOrderCancel, initialClaimCancel, nextOrderCancel, nextClaimCancel) => {
    const store = useOrdersStore()
    apiGet
      .mockResolvedValueOnce(orderListResponse([makeOrder({
        cancelPending: initialOrderCancel,
        claimCancelPending: initialClaimCancel,
      })]))
      .mockResolvedValueOnce(orderListResponse([makeOrder({
        cancelPending: nextOrderCancel,
        claimCancelPending: nextClaimCancel,
      })]))

    await store.fetchOrders()
    const previousOrders = store.orders
    const result = await store.fetchOrders({ silent: true })

    expect(result).toEqual({ success: true })
    expect(store.orders).not.toBe(previousOrders)
    expect(store.orders[0].cancel_pending).toBe(nextOrderCancel)
    expect(store.orders[0].my_claim.cancel_pending).toBe(nextClaimCancel)
  })

  it('keeps the existing list when all snapshot fields are unchanged', async () => {
    const store = useOrdersStore()
    apiGet
      .mockResolvedValueOnce(orderListResponse([makeOrder()]))
      .mockResolvedValueOnce(orderListResponse([makeOrder()]))

    await store.fetchOrders()
    const previousOrders = store.orders
    const result = await store.fetchOrders({ silent: true })

    expect(result).toEqual({ success: true, unchanged: true })
    expect(store.orders).toBe(previousOrders)
  })

  // 2026-11-11：WS 新单卡片带 accept_window_pending=true，第一次 2s/10s 对账
  // 补算出等待时间/拉黑标记时，即便 accept_available_at 仍为 null（保证金关闭
  // 或同一秒），也必须重渲；否则卡片会永远停在「等待时间加载中」。
  it('refreshes when accept_window_pending clears or accept_blocked appears', async () => {
    const store = useOrdersStore()
    const pendingCard = { ...makeOrder(), accept_window_pending: true }
    const resolvedCard = {
      ...makeOrder(),
      accept_window_pending: false,
      accept_blocked: true,
      accept_blocked_reason: '你已被该发布人拉黑，无法接此单',
    }
    apiGet
      .mockResolvedValueOnce(orderListResponse([pendingCard]))
      .mockResolvedValueOnce(orderListResponse([resolvedCard]))

    await store.fetchOrders()
    const previousOrders = store.orders
    const result = await store.fetchOrders({ silent: true })

    expect(result).toEqual({ success: true })
    expect(store.orders).not.toBe(previousOrders)
    expect(store.orders[0].accept_window_pending).toBe(false)
    expect(store.orders[0].accept_blocked).toBe(true)
  })
})
