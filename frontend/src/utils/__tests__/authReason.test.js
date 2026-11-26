import { describe, expect, it } from 'vitest'

import {
  DEFAULT_LOGOUT_REASON,
  loginNoticeForReason,
  normalizeLogoutReason,
  reasonFromError,
} from '../authReason'

function errorWithCode(code) {
  return { response: { data: { detail: { code, message: 'msg' } } } }
}

describe('authReason 登录失效原因映射', () => {
  it('maps every backend detail.code to the expected logout reason', () => {
    expect(reasonFromError(errorWithCode('device_limit'))).toBe('device-limit')
    expect(reasonFromError(errorWithCode('password_changed'))).toBe('password-changed')
    expect(reasonFromError(errorWithCode('admin_reset'))).toBe('password-reset')
    expect(reasonFromError(errorWithCode('disabled'))).toBe('account-disabled')
    expect(reasonFromError(errorWithCode('session_expired'))).toBe('session-expired')
    expect(reasonFromError(errorWithCode('session_invalidated'))).toBe(DEFAULT_LOGOUT_REASON)
  })

  it('falls back to session-invalidated for missing/unknown codes', () => {
    expect(reasonFromError(null)).toBe(DEFAULT_LOGOUT_REASON)
    expect(reasonFromError(new Error('network'))).toBe(DEFAULT_LOGOUT_REASON)
    expect(reasonFromError(errorWithCode('logout'))).toBe(DEFAULT_LOGOUT_REASON)
  })

  it('reads the detail.code from api interceptor wrapped errors too', () => {
    const wrapped = {
      status: 401,
      message: '密码已修改，请重新登录',
      original: errorWithCode('password_changed'),
    }
    expect(reasonFromError(wrapped)).toBe('password-changed')
  })

  it('normalizes only known logout reasons', () => {
    for (const reason of [
      'device-limit',
      'password-changed',
      'password-reset',
      'account-disabled',
      'session-expired',
      'session-invalidated',
    ]) {
      expect(normalizeLogoutReason(reason)).toBe(reason)
    }
    expect(normalizeLogoutReason('session-old')).toBeNull()
    expect(normalizeLogoutReason('')).toBeNull()
    expect(normalizeLogoutReason(null)).toBeNull()
    expect(normalizeLogoutReason(undefined)).toBeNull()
  })

  it('maps each reason to the exact login-page notice', () => {
    expect(loginNoticeForReason('device-limit')).toBe('账号已在其他设备登录，当前设备已下线。')
    expect(loginNoticeForReason('session-expired')).toBe('登录已过期，请重新登录。')
    expect(loginNoticeForReason('password-changed')).toBe('密码已修改，请重新登录。')
    expect(loginNoticeForReason('password-reset')).toBe('账号密码已被管理员重置，请重新登录。')
    expect(loginNoticeForReason('account-disabled')).toBe('账号已被停用，请联系管理员。')
    expect(loginNoticeForReason('session-invalidated')).toBe('登录状态已失效，请重新登录。')
    expect(loginNoticeForReason('unknown')).toBe('')
    expect(loginNoticeForReason(null)).toBe('')
  })
})
