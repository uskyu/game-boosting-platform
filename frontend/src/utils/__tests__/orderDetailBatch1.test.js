import { describe, expect, it } from 'vitest'

import {
  ORDER_STATUS_META,
  getClaimSettlementMeta,
  getOrderStatusBadgeClass,
} from '@/utils/order'

describe('需求6：「申请取消中」徽标改红', () => {
  it('CANCELLING 的 badgeClass 为 badge-disputed（红色）', () => {
    expect(ORDER_STATUS_META.CANCELLING.badgeClass).toBe('badge-disputed')
    expect(getOrderStatusBadgeClass('CANCELLING')).toBe('badge-disputed')
  })

  it('CANCELLING 标签与描述保持不变', () => {
    expect(ORDER_STATUS_META.CANCELLING.label).toBe('申请取消中')
    expect(ORDER_STATUS_META.CANCELLING.description).toBeTruthy()
  })

  it('不再复用 badge-review，且未改动其他状态的徽标', () => {
    expect(ORDER_STATUS_META.CANCELLING.badgeClass).not.toBe('badge-review')
    expect(ORDER_STATUS_META.DELIVERED.badgeClass).toBe('badge-review')
    expect(ORDER_STATUS_META.DISPUTED.badgeClass).toBe('badge-disputed')
  })
})

describe('需求5：getClaimSettlementMeta 的 settlement_paused 分支', () => {
  it('DELIVERED + 未审核 + settlement_paused 时显示倒计时已暂停', () => {
    const claim = {
      status: 'DELIVERED',
      approved_at: null,
      settlement_paused: true,
      settlement_due_at: '2999-01-01T00:00:00Z',
    }

    const meta = getClaimSettlementMeta(claim, Date.parse('2026-10-11T00:00:00Z'))

    expect(meta.label).toBe('协商/争议处理中，倒计时已暂停')
    expect(meta.tagClass).toBe('tag !bg-warning-soft !text-warning')
    expect(meta.countdown).toBeNull()
  })

  it('暂停时不标记为待审核/待结算（不进owner侧计数徽标）', () => {
    const meta = getClaimSettlementMeta({ status: 'DELIVERED', settlement_paused: true })

    expect(meta.isPendingReview).toBe(false)
    expect(meta.isPendingSettlement).toBe(false)
  })

  it('settlement_paused 为假时走原分支：有固定结算时间仍显示倒计时', () => {
    const now = Date.parse('2026-10-11T00:00:00Z')
    const claim = {
      status: 'DELIVERED',
      approved_at: null,
      settlement_paused: false,
      // 到账时间在 now 之后，formatDueCountdown 才会给出非空倒计时
      settlement_due_at: new Date(now + 3600 * 1000).toISOString(),
    }

    const meta = getClaimSettlementMeta(claim, now)

    expect(meta.label).toContain('自动入账钱包')
    expect(meta.tagClass).toBe('tag !bg-info-soft !text-info')
    expect(meta.isPendingReview).toBe(true)
    expect(meta.countdown).toBeTruthy()
  })

  it('settlement_paused 为假且无固定结算时间时仍显示待发布人审核', () => {
    const meta = getClaimSettlementMeta({ status: 'DELIVERED', approved_at: null })

    expect(meta.label).toBe('待发布人审核')
    expect(meta.tagClass).toBe('tag !bg-warning-soft !text-warning')
    expect(meta.isPendingReview).toBe(true)
  })

  it('已审核通过的 DELIVERED 不受 settlement_paused 影响', () => {
    const meta = getClaimSettlementMeta({
      status: 'DELIVERED',
      approved_at: '2026-10-10T00:00:00Z',
      settlement_paused: true,
    })

    expect(meta.label).toContain('审核通过')
    expect(meta.isPendingSettlement).toBe(true)
  })

  it('其他状态（SETTLED/CLAIMED）即使带 settlement_paused 也不进暂停分支', () => {
    expect(getClaimSettlementMeta({ status: 'SETTLED', settlement_paused: true }).label).toBe('已结算')
    expect(getClaimSettlementMeta({ status: 'CLAIMED', settlement_paused: true }).label).toBe('进行中')
  })
})
