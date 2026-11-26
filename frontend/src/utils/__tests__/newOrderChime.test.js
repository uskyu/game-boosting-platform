import { beforeEach, describe, expect, it, vi } from 'vitest'

const playNewOrder = vi.fn()
vi.mock('@/utils/sound', () => ({ playNewOrder: (...args) => playNewOrder(...args) }))

import {
  markNewOrderSoundHandled,
  playNewOrderForOrder,
  playNewOrderOnceForOrders,
  resetNewOrderChimeState,
} from '@/utils/newOrderChime'

beforeEach(() => {
  playNewOrder.mockReset()
  resetNewOrderChimeState()
})

describe('new order chime cross-path dedup', () => {
  it('plays once per order id and ignores repeats', () => {
    expect(playNewOrderForOrder(101)).toBe(true)
    expect(playNewOrder).toHaveBeenCalledTimes(1)
    expect(playNewOrderForOrder(101)).toBe(false)
    expect(playNewOrder).toHaveBeenCalledTimes(1)
  })

  it('batch reconciliation only rings once and marks every new order handled', () => {
    expect(playNewOrderOnceForOrders([201, 202])).toBe(true)
    expect(playNewOrder).toHaveBeenCalledTimes(1)
    // 通知路径随后到达同一批订单：不能再响
    expect(playNewOrderForOrder(201)).toBe(false)
    expect(playNewOrderForOrder(202)).toBe(false)
    expect(playNewOrder).toHaveBeenCalledTimes(1)
  })

  it('notification-first path suppresses the later hall reconciliation', () => {
    expect(playNewOrderForOrder(301)).toBe(true)
    expect(playNewOrderOnceForOrders([301])).toBe(false)
    expect(playNewOrder).toHaveBeenCalledTimes(1)
  })

  it('treats non-positive/empty ids as invalid and mark does not ring', () => {
    expect(playNewOrderForOrder(null)).toBe(false)
    expect(playNewOrderForOrder('')).toBe(false)
    markNewOrderSoundHandled('401')
    expect(playNewOrderForOrder(401)).toBe(false)
    expect(playNewOrder).not.toHaveBeenCalled()
  })
})
