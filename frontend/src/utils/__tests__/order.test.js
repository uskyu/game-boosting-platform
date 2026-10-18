import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import {
  CANCEL_PENDING_CLAIM_META,
  ORDER_STATUS_META,
  ORDER_STATUS_OPTIONS,
  getHallOrderDisplayBadgeClass,
  getHallOrderDisplayStatus,
  getOrderDisplayStatus,
  getOrderStatusLabel,
  isClaimCancelPending,
} from '@/utils/order'

const listSource = readFileSync(new URL('../../views/OrderList.vue', import.meta.url), 'utf8')

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

describe('售后中（AFTER_SALE）筛选值', () => {
  it('派单状态筛选项包含售后中，且放在已完成之后', () => {
    const values = ORDER_STATUS_OPTIONS.map((option) => option.value)
    expect(values).toContain('AFTER_SALE')
    expect(ORDER_STATUS_OPTIONS.find((option) => option.value === 'AFTER_SALE').label).toBe('售后中')
    expect(values.indexOf('AFTER_SALE')).toBeGreaterThan(values.indexOf('COMPLETED'))
  })

  it('AFTER_SALE 不是订单真实状态，不进 ORDER_STATUS_META', () => {
    expect(ORDER_STATUS_META.AFTER_SALE).toBeUndefined()
    // 走 fallback，证明没有被当成真实订单状态渲染徽标
    expect(getOrderStatusLabel('AFTER_SALE')).toBe('AFTER_SALE')
  })

  it('接单状态筛选也含售后中，且走后端过滤（fetchClaims 不对它特判）', () => {
    expect(listSource).toMatch(/\{ value: 'AFTER_SALE', label: '售后中' \}/)
    const fetchStart = listSource.indexOf('async function fetchClaims(')
    const fetchEnd = listSource.indexOf('function switchTab(')
    expect(fetchStart).toBeGreaterThan(-1)
    expect(fetchEnd).toBeGreaterThan(fetchStart)
    const fetchBody = listSource.slice(fetchStart, fetchEnd)
    // 只有 DISPUTED 被归一成 undefined（本地过滤），AFTER_SALE 必须原样透传给服务端。
    // 若有人把 AFTER_SALE 也加成"本地过滤"，这条断言会失败 —— 守住分页口径。
    expect(fetchBody).toMatch(/claimStatus\.value === 'DISPUTED' \? undefined : \(claimStatus\.value \|\| undefined\)/)
    expect(fetchBody).not.toMatch(/AFTER_SALE/)
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
