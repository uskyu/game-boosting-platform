import { describe, expect, it } from 'vitest'
import { cleanTemplatePayload, resetOrderForm } from '../orderTemplates'
import { formatShortDate } from '../display'

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
})
