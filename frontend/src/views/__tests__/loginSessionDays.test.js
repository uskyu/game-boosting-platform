import { readFileSync } from 'node:fs'

import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

function read(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const adminSettingsSource = read('../../components/admin/AdminSiteSettings.vue')
const siteStoreSource = read('../../stores/site.js')
const loginViewSource = read('../../views/LoginView.vue')
const appSource = read('../../App.vue')
const chatStoreSource = read('../../stores/chat.js')

function slice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing start marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('后台「登录保持天数」契约', () => {
  it('compiles AdminSiteSettings without SFC errors', () => {
    const { descriptor, errors } = parse(adminSettingsSource, {
      filename: 'AdminSiteSettings.vue',
    })
    expect(errors).toEqual([])
    expect(() => compileScript(descriptor, { id: 'AdminSiteSettings' })).not.toThrow()
  })

  it('adds login_session_days to defaults, sync and save payload', () => {
    expect(siteStoreSource).toMatch(/login_session_days:\s*7/)
    expect(adminSettingsSource).toMatch(/login_session_days:\s*7/)
    expect(adminSettingsSource).toMatch(
      /form\.login_session_days = Number\(store\.settings\.login_session_days \?\? 7\)/,
    )

    const saveBlock = slice(
      adminSettingsSource,
      'const r = await store.updateSettings({',
      'function addReplacement',
    )
    expect(saveBlock).toMatch(/login_session_days: normalizedLoginSessionDays\(\),/)
  })

  it('normalizes invalid input back to 7 and clamps to 1..365', () => {
    const normalizeBlock = slice(
      adminSettingsSource,
      'function normalizedLoginSessionDays()',
      'async function save()',
    )
    expect(normalizeBlock).toMatch(/raw === '' \|\| raw == null \? 7 : Number\(raw\)/)
    expect(normalizeBlock).toMatch(/if \(!Number\.isFinite\(value\)\) return 7/)
    expect(normalizeBlock).toMatch(/Math\.min\(Math\.max\(Math\.trunc\(value\), 1\), 365\)/)
  })

  it('renders the login-session-days input with required attributes and copy', () => {
    expect(adminSettingsSource).toMatch(/id="login-session-days"/)
    expect(adminSettingsSource).toMatch(/v-model\.number="form\.login_session_days"/)
    expect(adminSettingsSource).toMatch(/type="number"/)
    expect(adminSettingsSource).toMatch(/min="1"/)
    expect(adminSettingsSource).toMatch(/max="365"/)
    expect(adminSettingsSource).toMatch(/step="1"/)
    expect(adminSettingsSource).toMatch(/max-w-\[160px\]/)
    expect(adminSettingsSource).toMatch(/登录保持天数（天）/)
    expect(adminSettingsSource).toMatch(
      /控制登录后保持登录状态的时长（刷新令牌与设备会话有效期）；保存后对新登录生效，已登录用户保持原到期时间。/,
    )
  })
})

describe('登录失效提示使用统一原因映射', () => {
  it('LoginView no longer hardcodes a single invalid-session notice', () => {
    expect(loginViewSource).not.toMatch(/SESSION_EXPIRED_NOTICE/)
    expect(loginViewSource).not.toMatch(/DEVICE_LIMIT_NOTICE/)
    expect(loginViewSource).toMatch(
      /import \{ loginNoticeForReason, normalizeLogoutReason \} from '@\/utils\/authReason'/,
    )
    expect(loginViewSource).toMatch(
      /const queryReason = normalizeLogoutReason\(route\.query\.reason\)/,
    )
    expect(loginViewSource).toMatch(/if \(queryReason\) return loginNoticeForReason\(queryReason\)/)
    expect(loginViewSource).toMatch(
      /return loginNoticeForReason\(authStore\.lastLogoutReason\)/,
    )
  })

  it('App watcher keeps every normalized reason instead of only device-limit', () => {
    expect(appSource).toMatch(
      /import \{ normalizeLogoutReason \} from '@\/utils\/authReason'/,
    )
    expect(appSource).toMatch(
      /const reason = normalizeLogoutReason\(authStore\.lastLogoutReason\) \|\| 'session-expired'/,
    )
    expect(appSource).not.toMatch(
      /authStore\.lastLogoutReason === 'device-limit' \? 'device-limit' : 'session-expired'/,
    )
  })

  it('chat 4001 handling preserves interceptor-written reasons and falls back only when absent', () => {
    const handler = slice(
      chatStoreSource,
      'async function handleSessionInvalidated(authContext)',
      'async function handleWsMessage',
    )
    expect(handler).toMatch(
      /const reason = normalizeLogoutReason\(authStore\.lastLogoutReason\)/,
    )
    expect(handler).toMatch(
      /\(mapped === 'session-invalidated' \? 'session-expired' : mapped\)/,
    )
    expect(handler).not.toMatch(/logout\('session-expired'\)/)
  })
})
