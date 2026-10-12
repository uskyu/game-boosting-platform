const ALLOWED_EVENTS = new Set(['order_publish', 'order_accept', 'hall_refresh', 'order_notification'])
const ALLOWED_STAGES = new Set([
  'publish_total',
  'create_api',
  'attachment_uploads',
  'accept_api',
  'accept_detail_refresh',
  'hall_list_refresh',
  'ws_order_event',
  'new_order_notification',
])

/**
 * Measure one order-flow stage without retaining or logging request data.
 * Event and stage names are allowlisted so telemetry can only contain fixed labels.
 */
export async function measureOrderFlowStage(eventName, stage, operation, count = 1) {
  if (!ALLOWED_EVENTS.has(eventName) || !ALLOWED_STAGES.has(stage)) {
    throw new TypeError('Unknown order-flow timing event or stage')
  }
  if (typeof operation !== 'function') {
    throw new TypeError('Order-flow timing operation must be a function')
  }

  const safeCount = Number.isFinite(count) && count >= 0 ? Math.floor(count) : 0
  const startedAt = performance.now()
  try {
    return await operation()
  } finally {
    const duration = Math.max(0, performance.now() - startedAt)
    console.info('order_flow_timing', {
      event_name: eventName,
      stage,
      duration_ms: Math.round(duration * 100) / 100,
      count: safeCount,
    })
  }
}
