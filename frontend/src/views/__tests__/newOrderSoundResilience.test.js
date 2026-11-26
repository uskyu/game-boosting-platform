import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function source(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const appSource = source('../../App.vue')
const toastsSource = source('../../stores/toasts.js')
const notificationsSource = source('../../stores/notifications.js')
const ordersSource = source('../../stores/orders.js')
const soundSource = source('../../utils/sound.js')
const chimeSource = source('../../utils/newOrderChime.js')

describe('new-order sound resilience contracts', () => {
  it('reconciles notifications while WS claims to be connected and immediately after reconnect', () => {
    expect(appSource).toContain('NOTIF_CONNECTED_RECONCILE_INTERVAL')
    expect(appSource).toContain('Date.now() - lastNotifReconcileAt < NOTIF_CONNECTED_RECONCILE_INTERVAL')
    expect(appSource).toContain("const connected = chatStore.socketStatus === 'connected'")
    // 重连 watcher 里必须立即补拉，而不是只刷未读摘要
    expect(appSource).toContain('lastNotifReconcileAt = 0')
    expect(appSource).toContain('pollOrderNotifications({ generation: authGeneration })')
  })

  it('plays the hall-list fallback with order-id dedup shared with notifications', () => {
    expect(ordersSource).toContain("from '@/utils/newOrderChime'")
    expect(ordersSource).toContain('playNewOrderOnceForOrders(freshOrderIds)')
    expect(ordersSource).toContain('HALL_NEW_ORDER_SOUND_WINDOW_MS')
    expect(ordersSource).toContain("context === 'hall' && silent && previous.length > 0")
    expect(toastsSource).toContain('soundKey')
    expect(toastsSource).toContain('playNewOrderForOrder(soundKey)')
    expect(notificationsSource).toContain('soundKey: sound === \'new-order\' ? notificationOrderId(payload) : null')
    expect(chimeSource).toContain('playNewOrderOnceForOrders')
    expect(chimeSource).toContain('playedOrderIds')
  })

  it('falls back to WebAudio when HTML audio play() is rejected', () => {
    expect(soundSource).toContain('onFailure')
    expect(soundSource).toContain('typeof onFailure === \'function\'')
    expect(soundSource).toContain('playStaticAudio(src, slot.volume, () => webAudioFallback(slot.volume))')
    // 失败的 Promise 不能再被空 catch 吞掉
    expect(soundSource).not.toContain('result.catch(() => {})')
  })
})
