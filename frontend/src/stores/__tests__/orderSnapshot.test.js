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
})
