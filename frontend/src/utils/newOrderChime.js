/**
 * 新订单提示音的跨路径去重。
 *
 * 同一个新订单可能从两条路到达前端：
 * - WebSocket 实时通知（带 order_card，或走通知列表轮询兜底）；
 * - 大厅列表/摘要轮询对账发现新卡片（WS 丢失或半开时的兜底）。
 *
 * 两条路都必须能发声（否则 WS 丢包时用户只看到卡片、听不到语音），但同一
 * 订单只能响一次。这里用订单 ID 做全局去重；批量对账一次只响一声，避免
 * 多个新单同时补拉时叠音。
 */
import { playNewOrder } from '@/utils/sound'

const playedOrderIds = new Set()
const MAX_TRACKED_ORDER_IDS = 1000

function normalizeOrderId(orderId) {
  const value = String(orderId ?? '').trim()
  return value || ''
}

function pruneIfNeeded() {
  if (playedOrderIds.size <= MAX_TRACKED_ORDER_IDS) return
  const ids = [...playedOrderIds]
  ids.slice(0, ids.length - Math.floor(MAX_TRACKED_ORDER_IDS / 2)).forEach((id) => {
    playedOrderIds.delete(id)
  })
}

/** 单个订单到达（通知路径）：未响过才响，并标记为已处理。 */
export function playNewOrderForOrder(orderId) {
  const id = normalizeOrderId(orderId)
  if (!id || playedOrderIds.has(id)) return false
  playedOrderIds.add(id)
  pruneIfNeeded()
  playNewOrder()
  return true
}

/**
 * 批量订单到达（大厅对账路径）：把传入的新订单 ID 都标记为已处理；
 * 其中只要有一条此前没响过，就只响一声（同类新单合并提醒）。
 */
export function playNewOrderOnceForOrders(orderIds) {
  let hasUnplayed = false
  const ids = Array.isArray(orderIds) ? orderIds : [orderIds]
  ids.forEach((orderId) => {
    const id = normalizeOrderId(orderId)
    if (!id) return
    if (!playedOrderIds.has(id)) hasUnplayed = true
    playedOrderIds.add(id)
  })
  if (!hasUnplayed) return false
  pruneIfNeeded()
  playNewOrder()
  return true
}

/** 仅标记为已处理，不发声（例如通知已响过、大厅对账只补齐去重状态）。 */
export function markNewOrderSoundHandled(orderId) {
  const id = normalizeOrderId(orderId)
  if (!id) return
  playedOrderIds.add(id)
  pruneIfNeeded()
}

/** 登出/测试用：清空去重表。 */
export function resetNewOrderChimeState() {
  playedOrderIds.clear()
}
