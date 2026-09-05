export const ORDER_TEMPLATE_FIELDS = [
  'game_id', 'game_name', 'title', 'price', 'service_type', 'notes',
  'description_raw', 'boss_contact', 'max_claims', 'compensation_amount',
  'payout_delay_days', 'payout_delay_hours',
]

export function cleanTemplatePayload(fields = {}) {
  const payload = {}
  ORDER_TEMPLATE_FIELDS.forEach((key) => {
    const value = fields[key]
    if (value === undefined || value === null) return
    if (typeof value === 'string' && value.trim() === '') return
    payload[key] = typeof value === 'string' ? value.trim() : value
  })
  if (payload.compensation_amount === undefined) delete payload.compensation_amount
  return payload
}

export function resetOrderForm(defaults, template = {}) {
  const payload = template.payload || template
  const next = { ...defaults }
  ORDER_TEMPLATE_FIELDS.forEach((key) => {
    if (payload[key] !== undefined && payload[key] !== null && !(typeof payload[key] === 'string' && payload[key].trim() === '')) {
      next[key] = payload[key]
    }
  })
  next.compensation_enabled = payload.compensation_amount !== undefined && payload.compensation_amount !== null && payload.compensation_amount !== ''
  if (!next.compensation_enabled) next.compensation_amount = ''
  return next
}
