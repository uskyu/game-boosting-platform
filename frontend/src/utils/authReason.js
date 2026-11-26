/**
 * 登录失效原因的统一映射。
 *
 * 后端 401 的 detail 是结构化对象 {code, message}：
 * - code 表示为什么失效（设备超限/改密码/管理员重置/停用/过期/普通失效）；
 * - 前端把它归一化成固定 logout reason，登录页再映射成用户能看懂的文案。
 *
 * 这里集中维护「code → reason → 文案」三层关系，避免 api 拦截器、auth
 * store、chat store、App.vue 与 LoginView 各写一套字符串。
 */

const CODE_TO_REASON = {
  device_limit: 'device-limit',
  password_changed: 'password-changed',
  admin_reset: 'password-reset',
  disabled: 'account-disabled',
  session_expired: 'session-expired',
}

export const DEFAULT_LOGOUT_REASON = 'session-invalidated'

// 只接受已知原因：未知/空值一律视为「没有原因」，避免把任意字符串写进
// sessionStorage 后被登录页误判。
const KNOWN_REASONS = new Set([
  ...Object.values(CODE_TO_REASON),
  DEFAULT_LOGOUT_REASON,
])

const NOTICE_BY_REASON = {
  'device-limit': '账号已在其他设备登录，当前设备已下线。',
  'session-expired': '登录已过期，请重新登录。',
  'password-changed': '密码已修改，请重新登录。',
  'password-reset': '账号密码已被管理员重置，请重新登录。',
  'account-disabled': '账号已被停用，请联系管理员。',
  'session-invalidated': '登录状态已失效，请重新登录。',
}

function readErrorDetailCode(error) {
  const code = error?.response?.data?.detail?.code
    ?? error?.original?.response?.data?.detail?.code
  return typeof code === 'string' && code ? code : null
}

/** 从 401 错误里提取 logout reason；没有可识别 code 时按普通会话失效。 */
export function reasonFromError(error) {
  return CODE_TO_REASON[readErrorDetailCode(error)] || DEFAULT_LOGOUT_REASON
}

/** 归一化任意来源（query/store）的原因；不认识的值返回 null。 */
export function normalizeLogoutReason(value) {
  return typeof value === 'string' && KNOWN_REASONS.has(value) ? value : null
}

/** 登录页提示文案；未知/空原因返回空串（不显示提示）。 */
export function loginNoticeForReason(value) {
  const reason = normalizeLogoutReason(value)
  return reason ? NOTICE_BY_REASON[reason] : ''
}
