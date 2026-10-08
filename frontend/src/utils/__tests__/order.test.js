import { describe, expect, it } from 'vitest'

import {
  CANCEL_PENDING_CLAIM_META,
  ORDER_STATUS_META,
  ORDER_STATUS_OPTIONS,
  getHallOrderDisplayBadgeClass,
  getHallOrderDisplayStatus,
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

describe('订单大厅状态展示优先级', () => {
  it.each(['LOCKED', 'DELIVERED'])('满员活跃态 %s 的挂起取消协商优先显示 CANCELLING', (status) => {
    const order = {
      status,
      claim_status: 'FULL',
      claimed_count: 1,
      max_claims: 1,
      cancel_pending: true,
    }

    expect(getHallOrderDisplayStatus(order)).toBe('申请取消中')
    expect(getHallOrderDisplayBadgeClass(order)).toBe(ORDER_STATUS_META.CANCELLING.badgeClass)
  })

  it('暂停、截止与名额满员不会覆盖活跃态的取消协商', () => {
    expect(getHallOrderDisplayStatus({
      status: 'LOCKED', cancel_pending: true, claim_status: 'PAUSED',
    })).toBe('申请取消中')
    expect(getHallOrderDisplayBadgeClass({
      status: 'DELIVERED', cancel_pending: true, claim_status: 'CLOSED',
    })).toBe(ORDER_STATUS_META.CANCELLING.badgeClass)
    expect(getHallOrderDisplayStatus({
      status: 'LOCKED', cancel_pending: true, deadline: '2000-01-01T00:00:00Z',
    })).toBe('申请取消中')
  })

  it('归档状态仍优先于挂起协商', () => {
    const archivedOrder = { status: 'LOCKED', cancel_pending: true, is_archived: true }
    expect(getHallOrderDisplayStatus(archivedOrder)).toBe('已归档')
    expect(getHallOrderDisplayBadgeClass(archivedOrder)).toBe('badge-cancelled')
  })

  it('非活跃状态不会因取消标记显示为 CANCELLING', () => {
    expect(getHallOrderDisplayStatus({
      status: 'PENDING', cancel_pending: true, claim_status: 'FULL', claimed_count: 1, max_claims: 1,
    })).toBe('已满员')
    expect(getHallOrderDisplayBadgeClass({ status: 'PENDING', cancel_pending: true })).toBe('badge-pending')
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
