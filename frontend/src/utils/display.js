const priceFormatter = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
})

const shortDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

const fullDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const countFormatter = new Intl.NumberFormat('zh-CN')

function parseDate(value) {
  if (!value) {
    return null
  }

  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatPrice(value) {
  const numericValue = Number(value ?? 0)
  return priceFormatter.format(Number.isNaN(numericValue) ? 0 : numericValue)
}

/**
 * 订单价格统一展示：仅当 price_min 与 price_max 都存在且不相等时显示区间，
 * 否则显示单一价 price（后端单一价会把 min/max 冗余存成同值，需按单一价显示）。
 */
export function formatOrderPrice(order) {
  if (!order) return formatPrice(0)
  const min = order.price_min ?? order.min_price
  const max = order.price_max ?? order.max_price
  if (min != null && max != null && Number(min) !== Number(max)) {
    return `${formatPrice(min)} ~ ${formatPrice(max)}`
  }
  return formatPrice(order.price ?? min ?? max ?? 0)
}

export function formatShortDate(value) {
  const parsed = parseDate(value)
  return parsed ? shortDateFormatter.format(parsed) : '未记录'
}

export function formatDateTime(value) {
  const parsed = parseDate(value)
  return parsed ? fullDateFormatter.format(parsed) : '未记录'
}

export function formatDate(value) {
  const parsed = parseDate(value)
  return parsed ? dateFormatter.format(parsed) : '未记录'
}

export function formatCount(value) {
  const numericValue = Number(value ?? 0)
  return countFormatter.format(Number.isNaN(numericValue) ? 0 : numericValue)
}
