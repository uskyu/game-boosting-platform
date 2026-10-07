import { describe, expect, it } from 'vitest'

import {
  CANCEL_PENDING_CLAIM_META,
  ORDER_STATUS_META,
  ORDER_STATUS_OPTIONS,
  getOrderDisplayStatus,
  isClaimCancelPending,
} from '@/utils/order'

describe('申请取消中（CANCELLING）状态展示', () => {
  it('筛选项包含申请取消中且放在进行中之后', () => {
    const values = ORDER_STATUS_OPTIONS.map((option) => option.value)
    expect(values).toContain('CANCELLING')
    expect(values.indexOf('CANCELLING')).toBeGreaterThan(values.indexOf('LOCKED'))
    expect(ORDER_STATUS_OPTIONS.find((option) => option.value === 'CANCELLING').label).toBe('申请取消中')
  })

  it('有取消协商挂起的进行中/待确认订单对外显示 CANCELLING', () => {
    expect(getOrderDisplayStatus({ status: 'LOCKED', cancel_pending: true })).toBe('CANCELLING')
    expect(getOrderDisplayStatus({ status: 'DELIVERED', cancel_pending: true })).toBe('CANCELLING')
  })

  it('没有挂起或已完结的订单保持原状态', () => {
    expect(getOrderDisplayStatus({ status: 'LOCKED', cancel_pending: false })).toBe('LOCKED')
    expect(getOrderDisplayStatus({ status: 'PENDING', cancel_pending: true })).toBe('PENDING')
    expect(getOrderDisplayStatus({ status: 'COMPLETED', cancel_pending: true })).toBe('COMPLETED')
    expect(getOrderDisplayStatus({ status: 'CANCELLED', cancel_pending: true })).toBe('CANCELLED')
    expect(getOrderDisplayStatus(undefined)).toBeUndefined()
  })

  it('CANCELLING 有中文标签与徽标样式', () => {
    expect(ORDER_STATUS_META.CANCELLING.label).toBe('申请取消中')
    expect(ORDER_STATUS_META.CANCELLING.badgeClass).toBeTruthy()
  })
})

describe('名额级取消协商标记', () => {
  it('CLAIMED/DELIVERED 名额有挂起协商时判定为待取消', () => {
    expect(isClaimCancelPending({ status: 'CLAIMED', cancel_pending: true })).toBe(true)
    expect(isClaimCancelPending({ status: 'DELIVERED', cancel_pending: true })).toBe(true)
  })

  it('已结束的名额或没有挂起时不判定', () => {
    expect(isClaimCancelPending({ status: 'SETTLED', cancel_pending: true })).toBe(false)
    expect(isClaimCancelPending({ status: 'CANCELLED', cancel_pending: true })).toBe(false)
    expect(isClaimCancelPending({ status: 'CLAIMED', cancel_pending: false })).toBe(false)
    expect(isClaimCancelPending(undefined)).toBe(false)
  })

  it('待取消名额有统一的中文标签', () => {
    expect(CANCEL_PENDING_CLAIM_META.label).toBe('申请取消中')
  })
})
