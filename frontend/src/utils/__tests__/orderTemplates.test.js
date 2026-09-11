import { describe, expect, it } from 'vitest'
import { cleanTemplatePayload, resetOrderForm } from '../orderTemplates'
import { formatSettlementDelay, formatShortDate, getAcceptWaitMeta } from '../display'

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
