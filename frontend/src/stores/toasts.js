import { ref } from 'vue'
import { defineStore } from 'pinia'
import { playClaimed, playNewOrder } from '@/utils/sound'

// 网页内轻提示（新消息推送等）：仅在新事件时弹出，几秒自动消失，可点 X 手动关
let seq = 0

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

  // sound: 'new-order'（来单一声）| 'claimed'（被接手两声）| null（静默）
  function pushToast({ title, body = '', duration = 4500, to = null, sound = null }) {
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
      playNewOrder()
    } else if (sound === 'claimed') {
      playClaimed()
    }
    return id
  }

  return { toasts, pushToast, dismissToast }
})
