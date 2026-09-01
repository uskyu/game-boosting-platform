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
