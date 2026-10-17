/**
 * 售后兜底（「联系平台协助」）预设文案。
 *
 * 打手暂时联系不上发单人时，三个入口（钱包页 / 订单详情 / 我的接单列表）
 * 都通过 chatStore.startAdminConversation 直达平台管理员会话，并用本模块
 * 生成同一条预填案情，避免各处文案漂移。
 */

// 售后金额是 Decimal，序列化后可能是 "50.00" 这类字符串；统一转成数字，
// 取不到或非法时返回 null，由调用方省略金额片段（而不是写出 "¥undefined"）。
function normalizeAmount(amount) {
  if (amount == null || amount === '') {
    return null
  }
  const numericAmount = Number(amount)
  return Number.isFinite(numericAmount) ? numericAmount : null
}

/**
 * 生成售后兜底的预填消息。
 *
 * @param {{ orderId: number|string, amount?: number|string|null }} options
 * @returns {string} 有金额：「订单 #12 的售后申请（¥50）我暂时联系不上发单人，请平台协助裁定结案。」
 *                  无金额：「订单 #12 的售后申请我暂时联系不上发单人，请平台协助裁定结案。」
 */
export function buildAfterSaleAssistMessage({ orderId, amount } = {}) {
  const amountText = (() => {
    const numericAmount = normalizeAmount(amount)
    return numericAmount == null ? '' : `（¥${numericAmount}）`
  })()
  return `订单 #${orderId} 的售后申请${amountText}我暂时联系不上发单人，请平台协助裁定结案。`
}
