import { describe, expect, it } from 'vitest'
import { cleanTemplatePayload, resetOrderForm } from '../orderTemplates'
import { DEFAULT_PAYOUT_DELAY_DAYS, formatPayoutDelay, formatSettlementDelay, formatShortDate, getAcceptWaitMeta, parsePayoutDelay, payoutDelayInfo } from '../display'

describe('order template contract', () => {
  it('trims strings, omits blanks, preserves zero, and excludes unknown fields', () => {
    expect(cleanTemplatePayload({ title: '  title ', price: 0, payout_delay_days: 0, notes: ' ', attachments: ['x'] })).toEqual({ title: 'title', price: 0, payout_delay_days: 0 })
  })

  it('resets to empty defaults and keeps current attachments while applying payload', () => {
    const defaults = { game_id: null, title: '', price: '', boss_contact: '', compensation_enabled: false, compensation_amount: '', attachments: ['current'] }
    expect(resetOrderForm(defaults, { payload: { game_id: 2, title: 'New', compensation_amount: 0 } })).toEqual({ ...defaults, game_id: 2, title: 'New', compensation_enabled: true, compensation_amount: 0 })
  })

  it('clears compensation residue when template has no amount', () => {
    expect(resetOrderForm({ compensation_enabled: true, compensation_amount: 20, attachments: null }, { payload: { title: 'x' } })).toEqual({ compensation_enabled: false, compensation_amount: '', attachments: null, title: 'x' })
  })

  it('interprets API DATETIME without offset as Shanghai wall-clock time', () => {
    expect(formatShortDate('2026-09-06T20:22:27')).toContain('20:22')
  })

  it('renders a zero-hour settlement snapshot as immediate', () => {
    expect(formatSettlementDelay(0)).toBe('立即结算')
    expect(formatSettlementDelay(24)).toBe('1天')
    expect(formatSettlementDelay(25)).toBe('1天1小时')
  })

  it('留空（null）不得被当成 0 显示成「立即结算」', () => {
    // Number(null) === 0，若先转数字会把「按订单时效」错显成「立即结算」
    expect(formatSettlementDelay(null)).toBe('')
    expect(formatSettlementDelay(undefined)).toBe('')
    expect(formatSettlementDelay('')).toBe('')
  })

  it('到账时效留空发 null（由后端按默认 3 天兜底），不代填 0', () => {
    expect(DEFAULT_PAYOUT_DELAY_DAYS).toBe(3)
    // 两端都留空 → 发 null/null，交给后端回落默认；若发 0 会变成「交付即结算」
    expect(parsePayoutDelay('', '')).toEqual({ days: null, hours: null })
    expect(parsePayoutDelay(null, null)).toEqual({ days: null, hours: null })
    // 显式 0 天 0 小时仍报错
    expect(parsePayoutDelay(0, 0).error).toContain('3 天')
  })

  it('订单没填到账时效时展示默认 3 天，绝不返回空串', () => {
    // 老板 2026-10-18：返回空串会让「到账时效」整块被 v-if 隐藏，
    // 用户看到的就是"这个订单没有到账时间"。
    for (const blank of [
      { payout_delay_days: null, payout_delay_hours: null },
      {},
      { payout_delay_days: '', payout_delay_hours: '' },
      { payout_delay_days: 0, payout_delay_hours: 0 },
    ]) {
      expect(formatPayoutDelay(blank)).toBe('3天')
      expect(payoutDelayInfo(blank)).toEqual({ text: '3天', isDefault: true })
    }
    // null / undefined 订单也不能抛
    expect(formatPayoutDelay(null)).toBe('3天')
    expect(formatPayoutDelay(undefined)).toBe('3天')
  })

  it('订单显式填了时效就按填的显示，且不标「默认」', () => {
    expect(payoutDelayInfo({ payout_delay_days: 5 })).toEqual({ text: '5天', isDefault: false })
    expect(payoutDelayInfo({ payout_delay_days: 3 })).toEqual({ text: '3天', isDefault: false })
    expect(payoutDelayInfo({ payout_delay_hours: 12 })).toEqual({ text: '12小时', isDefault: false })
    expect(payoutDelayInfo({ payout_delay_days: 1, payout_delay_hours: 12 })).toEqual({
      text: '1天12小时',
      isDefault: false,
    })
    // 显式填 3 天与"没填按默认 3 天"文案相同，但 isDefault 必须能区分
    expect(formatPayoutDelay({ payout_delay_days: null, payout_delay_hours: null })).toBe(
      formatPayoutDelay({ payout_delay_days: 3 }),
    )
    expect(payoutDelayInfo({ payout_delay_days: 3 }).isDefault).toBe(false)
    expect(payoutDelayInfo({}).isDefault).toBe(true)
  })

  it('distinguishes remaining wait from configured total wait', () => {
    const now = Date.parse('2026-09-10T12:00:00Z')
    const order = {
      accept_wait_seconds: 30,
      accept_available_at: new Date(now + 5000).toISOString(),
    }
    expect(getAcceptWaitMeta(order, now)).toEqual({ remaining: 5, total: 30, state: 'waiting' })
    expect(getAcceptWaitMeta({ ...order, accept_available_at: new Date(now - 1000).toISOString() }, now)).toEqual({
      remaining: 0,
      total: 30,
      state: 'ready',
    })
    expect(getAcceptWaitMeta({}, now)).toEqual({ remaining: 0, total: 0, state: 'available' })
  })
})
