import { ref } from 'vue'
import { defineStore } from 'pinia'
import { playNewOrderForOrder } from '@/utils/newOrderChime'
import { playCancelRequested, playChatMessage, playClaimed, playNewOrder } from '@/utils/sound'

// 网页内轻提示（新消息推送等）：仅在新事件时弹出，几秒自动消失，可点 X 手动关
let seq = 0
const announcedKeys = new Set()

export const useToastsStore = defineStore('toasts', () => {
  const toasts = ref([])
  const timers = new Map()

  function dismissToast(id) {
    const timer = timers.get(id)
    if (timer) {
      window.clearTimeout(timer)
      timers.delete(id)
    }
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }

  function resetState() {
    timers.forEach((timer) => window.clearTimeout(timer))
    timers.clear()
    toasts.value = []
    announcedKeys.clear()
  }

  // sound: 'new-order'（来单一声）| 'claimed'（被接手两声）| 'cancel-requested'（取消申请播报）| null（静默）
  // dedupKey: 可选去重键，重复传入直接 return -1，不重复弹、不重复响
  // soundKey: 新订单提示音的跨路径去重键（订单 ID）；大厅列表对账也会用同一 ID 去重
  function pushToast({
    title,
    body = '',
    duration = 4500,
    to = null,
    sound = null,
    dedupKey = null,
    soundKey = null,
  }) {
    if (dedupKey != null) {
      if (announcedKeys.has(dedupKey)) {
        return -1
      }
      announcedKeys.add(dedupKey)
      if (announcedKeys.size > 200) {
        const keys = [...announcedKeys]
        keys.slice(0, Math.floor(keys.length / 2)).forEach((k) => announcedKeys.delete(k))
      }
    }
    const id = ++seq
    toasts.value.push({ id, title, body, to })
    if (toasts.value.length > 3) {
      const oldest = toasts.value[0]
      if (timers.has(oldest.id)) {
        window.clearTimeout(timers.get(oldest.id))
        timers.delete(oldest.id)
      }
      toasts.value.shift()
    }
    timers.set(id, window.setTimeout(() => dismissToast(id), duration))
    if (sound === 'new-order') {
      // 有订单 ID 时走跨路径去重：WS 通知与大厅列表对账不能对同一单响两次。
      if (soundKey != null) playNewOrderForOrder(soundKey)
      else playNewOrder()
    } else if (sound === 'claimed') {
      playClaimed()
    } else if (sound === 'cancel-requested') {
      playCancelRequested()
    } else if (sound === 'chat-message') {
      playChatMessage()
    }
    return id
  }

  return { toasts, pushToast, dismissToast, resetState }
})
