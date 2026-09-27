import { formatDateTime, parseDate, serverNow } from '@/utils/display'

export const WITHDRAWAL_MIN_AMOUNT = 1
export const WITHDRAWAL_INTERVAL_MIN_HOURS = 1
export const WITHDRAWAL_INTERVAL_MAX_HOURS = 168

export const REFRESH_MODE_META = {
  INTERVAL: { label: '每隔 N 小时刷新' },
  DAILY_NOON: { label: '每天 12:00 刷新' },
}

// 兜底规则与后端默认值保持一致：后台没配过也按 24 小时一个周期展示
const DEFAULT_INTERVAL_HOURS = 24

export function isWholeYuanAmount(value) {
  const amount = Number(value)
  return Number.isFinite(amount) && Number.isInteger(amount)
}

/**
 * 提现金额校验：金额以元为单位且必须为整数（避免出现 10.5 元这种无法精确
 * 结算的申请）。availableBalance 不是有限数（如 undefined）时跳过超额判断，
 * 只校验金额本身。
 */
export function validateWithdrawAmount(rawAmount, availableBalance) {
  const raw = typeof rawAmount === 'string' ? rawAmount.trim() : rawAmount
  const amount = Number(raw)
  if (raw === '' || raw === null || raw === undefined || !Number.isFinite(amount)) {
    return '请填写提现金额'
  }
  if (amount < WITHDRAWAL_MIN_AMOUNT) {
    return `提现金额不能低于 ${WITHDRAWAL_MIN_AMOUNT} 元`
  }
  if (!isWholeYuanAmount(amount)) {
    return '提现金额必须为整数（元）'
  }
  const balance = Number(availableBalance)
  if (Number.isFinite(balance) && amount > balance) {
    return '提现金额不能超过可用余额'
  }
  return ''
}

/**
 * 刷新规则一句话说明：INTERVAL 按每个用户自己的上次提现时间起算，
 * DAILY_NOON 全站统一在每天 12:00 刷新。规则缺失或小时间隔非法时回落默认值，
 * 避免后台返回脏数据时前端展示出空句。
 */
export function describeRefreshRule(rule) {
  if (rule?.mode === 'DAILY_NOON') {
    return '每天 12:00 刷新一次'
  }
  const hours = Number(rule?.interval_hours)
  if (!Number.isInteger(hours) || hours < WITHDRAWAL_INTERVAL_MIN_HOURS) {
    return `每隔 ${DEFAULT_INTERVAL_HOURS} 小时刷新一次`
  }
  return `每隔 ${hours} 小时刷新一次（按上次提现时间起算）`
}

/**
 * 提现机会提示：available !== false 表示当前有 1 次机会；
 * 否则只有 next_refresh_at 在未来才禁用提交（到点即视为已恢复，不依赖页面刷新，
 * 调用方每秒传 now 过来就能自动解除）。
 * 模式 B 的 next_refresh_at 指「下一轮刷新时间」，是机会已经用完时才拿来禁用
 * 按钮的参考值；available 为 true 时它只是下次刷新预告，不能让按钮被禁用。
 */
export function quotaNotice(quota, now = serverNow()) {
  if (!quota) {
    return { blocked: false, text: '' }
  }
  const availableText = `当前有 1 次提现机会，${describeRefreshRule(quota)}`
  if (quota.available !== false) {
    return { blocked: false, text: availableText }
  }
  const nextAt = parseDate(quota.next_refresh_at)
  if (!nextAt || now >= nextAt.getTime()) {
    return { blocked: false, text: availableText }
  }
  return {
    blocked: true,
    text: `提现机会已用完，${formatDateTime(quota.next_refresh_at)} 后可再次申请，${describeRefreshRule(quota)}`,
  }
}

// 用 formatToParts 逐段取年月日再拼 YYYY-MM-DD：各 locale 的默认输出格式
// 不一致（zh-CN 是斜杠、en-CA 是连字符），只认字段名才稳定。
const shanghaiDayPartsFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

// parseDate 只认接口返回的字符串；这里还要接纳 Date 实例与 serverNow() 的毫秒数
function toDate(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'number') {
    return Number.isFinite(value) ? new Date(value) : null
  }
  return parseDate(value)
}

export function shanghaiDayKey(value) {
  const parsed = toDate(value)
  if (!parsed) {
    return ''
  }
  const parts = shanghaiDayPartsFormatter.formatToParts(parsed)
  const pick = (type) => parts.find((part) => part.type === type)?.value || ''
  return `${pick('year')}-${pick('month')}-${pick('day')}`
}

/**
 * 今天（上海时区）是否已有提现记录：被驳回的申请不占机会，直接跳过。
 * 提现机会按上海自然日 / 用户自有节奏计算，与展示时区保持同一口径。
 */
export function findTodayWithdrawal(withdrawals, now = serverNow()) {
  if (!Array.isArray(withdrawals)) {
    return null
  }
  const todayKey = shanghaiDayKey(now)
  if (!todayKey) {
    return null
  }
  return (
    withdrawals.find((item) => shanghaiDayKey(item?.created_at) === todayKey && item?.status !== 'REJECTED') ||
    null
  )
}
