import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
vi.mock('@/utils/api', () => ({
  default: { get: (...args) => apiGet(...args), post: (...args) => apiPost(...args) },
  refreshAccessToken: vi.fn(),
}))
vi.mock('@/utils/sound', () => ({
  playChatMessage: vi.fn(),
  playClaimed: vi.fn(),
  playNewOrder: vi.fn(),
  playCancelRequested: vi.fn(),
}))

// toast store 用 mock 替身：记录 pushToast 入参（断言 sound 键），并按真实 store 的
// 分流逻辑把声音派发到被 mock 的播音函数——一次覆盖 notifications 的 sound 映射
// 与 toasts 的播放分流两处改动。
const pushToast = vi.fn(({ sound } = {}) => {
  if (sound === 'new-order') playNewOrder()
  else if (sound === 'claimed') playClaimed()
  else if (sound === 'cancel-requested') playCancelRequested()
  else if (sound === 'chat-message') playChatMessage()
})
vi.mock('@/stores/toasts', () => ({
  useToastsStore: () => ({ pushToast }),
}))

import { playCancelRequested, playClaimed, playNewOrder } from '@/utils/sound'
import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import { useSettingsStore } from '@/stores/settings'

beforeEach(() => {
  vi.clearAllMocks()
  apiGet.mockReset().mockResolvedValue({ data: { total_unread: 0, items: [] } })
  apiPost.mockReset()
  vi.stubGlobal('location', { protocol: 'https:', host: 'example.test' })
  vi.stubGlobal('document', { visibilityState: 'visible' })
  const windowListeners = new Map()
  vi.stubGlobal('window', {
    setInterval: (...args) => globalThis.setInterval(...args),
    clearInterval: (...args) => globalThis.clearInterval(...args),
    setTimeout: (...args) => globalThis.setTimeout(...args),
    clearTimeout: (...args) => globalThis.clearTimeout(...args),
    addEventListener: (name, listener) => windowListeners.set(name, listener),
    removeEventListener: (name, listener) => windowListeners.delete(name, listener),
    dispatchEvent: (event) => windowListeners.get(event.type)?.(event),
  })
  vi.stubGlobal('localStorage', {
    values: new Map(),
    getItem(key) { return this.values.get(key) ?? null },
    setItem(key, value) { this.values.set(key, String(value)) },
    removeItem(key) { this.values.delete(key) },
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.setTokens('test-access-token', 'test-refresh-token')
  auth.setUser({ id: 1, role: 'ADMIN' })
  globalThis.__testPinia = pinia
})

afterEach(() => {
  disposePinia(globalThis.__testPinia)
  delete globalThis.__testPinia
  vi.unstubAllGlobals()
})

describe('announceOrderNotification cancel-request broadcast', () => {
  function announce(payload, options) {
    useNotificationsStore().announceOrderNotification(payload, options)
  }

  it('routes ORDER_CANCEL_REQUESTED to the cancel-requested 播报', () => {
    // 需求：订单被申请取消时播「有订单申请取消了请及时查看」
    announce({
      id: 101,
      type: 'ORDER_CANCEL_REQUESTED',
      title: '打手申请取消待你确认',
      content: '打手对订单「王者荣耀」提出取消协商',
      link: '/orders/9',
    })

    expect(pushToast).toHaveBeenCalledTimes(1)
    const toast = pushToast.mock.calls[0][0]
    expect(toast.sound).toBe('cancel-requested')
    expect(toast.title).toBe('打手申请取消待你确认')
    expect(toast.body).toBe('打手对订单「王者荣耀」提出取消协商')
    expect(toast.to).toBe('/orders/9')
    expect(toast.dedupKey).toBe('notif-101')
    expect(playCancelRequested).toHaveBeenCalledTimes(1)
  })

  it('keeps new-order announcements on the new-order sound', () => {
    announce({ id: 102, type: 'SYSTEM_ANNOUNCEMENT', title: '新订单提醒：王者荣耀', content: '有人发布了新订单' })

    expect(pushToast.mock.calls[0][0].sound).toBe('new-order')
    expect(playNewOrder).toHaveBeenCalledTimes(1)
    expect(playCancelRequested).not.toHaveBeenCalled()
  })

  it('keeps ORDER_ACCEPTED on the claimed sound', () => {
    announce({ id: 103, type: 'ORDER_ACCEPTED', title: '订单已被接手' })

    expect(pushToast.mock.calls[0][0].sound).toBe('claimed')
    expect(playClaimed).toHaveBeenCalledTimes(1)
    expect(playCancelRequested).not.toHaveBeenCalled()
  })

  it('stays silent for unrelated notification types', () => {
    announce({ id: 104, type: 'SYSTEM_ANNOUNCEMENT', title: '系统维护通知' })

    expect(pushToast).not.toHaveBeenCalled()
    expect(playCancelRequested).not.toHaveBeenCalled()
  })

  it('respects global DND and the per-type preference switch', () => {
    const settings = useSettingsStore()

    settings.preferences = { notification_settings: { global_dnd: true } }
    announce({ id: 105, type: 'ORDER_CANCEL_REQUESTED', title: 'x' })
    expect(pushToast).not.toHaveBeenCalled()

    settings.preferences = { notification_settings: { ORDER_CANCEL_REQUESTED: false } }
    announce({ id: 106, type: 'ORDER_CANCEL_REQUESTED', title: 'x' })
    expect(pushToast).not.toHaveBeenCalled()

    // 开关恢复后重新播报
    settings.preferences = { notification_settings: { ORDER_CANCEL_REQUESTED: true } }
    announce({ id: 107, type: 'ORDER_CANCEL_REQUESTED', title: 'x' })
    expect(pushToast).toHaveBeenCalledTimes(1)
    expect(pushToast.mock.calls[0][0].sound).toBe('cancel-requested')
  })

  it('does not announce to anonymous visitors', () => {
    useAuthStore().logout()

    announce({ id: 108, type: 'ORDER_CANCEL_REQUESTED', title: 'x' })

    expect(pushToast).not.toHaveBeenCalled()
  })

  it('skips announcements when the request context goes stale', () => {
    announce(
      { id: 109, type: 'ORDER_CANCEL_REQUESTED', title: 'x' },
      { isCurrent: () => false },
    )

    expect(pushToast).not.toHaveBeenCalled()
  })
})
