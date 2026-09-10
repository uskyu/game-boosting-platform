const priceFormatter = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
})

const DISPLAY_TIME_ZONE = 'Asia/Shanghai'

const shortDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: DISPLAY_TIME_ZONE,
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

const fullDateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: DISPLAY_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: DISPLAY_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const countFormatter = new Intl.NumberFormat('zh-CN')

export function parseDate(value) {
  if (!value) {
    return null
  }

  const raw = String(value).trim()
  // MySQL DATETIME API 值没有 offset，当前数据库约定保存上海墙钟时间；
  // 先按 Asia/Shanghai 解释，再交给 Intl 的上海时区 formatter，避免少/多 8 小时。
  const hasExplicitZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw)
  const parsed = hasExplicitZone
    ? new Date(raw)
    : /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$/.test(raw)
      ? new Date(`${raw.replace(' ', 'T')}+08:00`)
      : new Date(raw)
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

/**
 * 接单等待剩余秒数：基于 accept_available_at（ISO 8601 UTC）与当前时间求差。
 * 返回 0 表示可立即接单（字段为空 / 非法 / 已过期）；否则返回向上取整的剩余秒数。
 * 倒计时只做相对计算，前端用本机时间即可。
 */
export function getAcceptWaitSeconds(availableAt, now = Date.now()) {
  if (!availableAt) return 0
  const target = new Date(availableAt)
  if (Number.isNaN(target.getTime())) return 0
  const diff = target.getTime() - now
  return diff > 0 ? Math.ceil(diff / 1000) : 0
}

export function formatCount(value) {
  const numericValue = Number(value ?? 0)
  return countFormatter.format(Number.isNaN(numericValue) ? 0 : numericValue)
}

/**
 * 到账时效统一展示：X天Y小时（如 1天 / 12小时 / 1天12小时 / 3天）。
 * 天和小时都为空（或都为 0）时返回空字符串 = 不设置。
 */
export function formatPayoutDelay(order) {
  if (!order) return ''
  const days = Number(order.payout_delay_days)
  const hours = Number(order.payout_delay_hours)
  const hasDays = Number.isFinite(days) && days > 0
  const hasHours = Number.isFinite(hours) && hours > 0
  if (!hasDays && !hasHours) return ''
  const daysText = hasDays ? `${days}天` : ''
  const hoursText = hasHours ? `${hours}小时` : ''
  return `${daysText}${hoursText}`
}

/**
 * 到账时效表单解析：两个 number input（天 0-30，小时 0-23）→ payload。
 * 返回 { days, hours }（null = 不设置）或 { error }。
 */
export function parsePayoutDelay(daysRaw, hoursRaw) {
  const daysEmpty = daysRaw === '' || daysRaw == null
  const hoursEmpty = hoursRaw === '' || hoursRaw == null
  if (daysEmpty && hoursEmpty) return { days: null, hours: null }
  const days = daysEmpty ? 0 : Number(daysRaw)
  const hours = hoursEmpty ? 0 : Number(hoursRaw)
  if (!Number.isInteger(days) || days < 0 || days > 30) {
    return { error: '到账天数需为 0-30 的整数' }
  }
  if (!Number.isInteger(hours) || hours < 0 || hours > 23) {
    return { error: '到账小时需为 0-23 的整数' }
  }
  if (days === 0 && hours === 0) {
    return { error: '到账时效需至少设置天数或小时数（不设置请都留空）' }
  }
  return { days: daysEmpty ? null : days, hours: hoursEmpty ? null : hours }
}
