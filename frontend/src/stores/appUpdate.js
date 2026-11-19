import { defineStore } from 'pinia'
import { ref } from 'vue'

import { extractModuleEntry } from '@/utils/appVersion'

/**
 * 检测服务器上是否已有更新版本的前端入口（index.html 引用的 index-*.js）。
 *
 * 长驻页面（微信 XWEB、移动端后台恢复）不会主动重新拉 index.html；如果期间
 * 发过版，旧页面会继续用旧代码，甚至遇到 chunk 404。这个 store 在应用启动
 * 时记录当前入口，运行期每 5 分钟 + 页面回到前台时静默比对服务器 index.html，
 * 不一致就标记 available，由 AppUpdateBanner 提示刷新；发单页据此拒绝提交。
 */
const CHECK_INTERVAL_MS = 5 * 60 * 1000

function currentEntryFromDom() {
  const script = document.querySelector('script[type="module"][src]')
  return script?.getAttribute('src') || ''
}

export const useAppUpdateStore = defineStore('appUpdate', () => {
  const available = ref(false)
  let baseline = ''
  let timer = null
  let started = false
  let visibilityHandler = null

  async function checkNow() {
    if (available.value) return
    if (!import.meta.env.PROD) return
    if (!baseline) baseline = currentEntryFromDom()
    if (!baseline) return
    try {
      // no-store 避免中间层/浏览器缓存旧 index.html
      const response = await fetch(`/index.html?ts=${Date.now()}`, { cache: 'no-store' })
      if (!response.ok) return
      const next = extractModuleEntry(await response.text())
      if (next && next !== baseline) {
        available.value = true
      }
    } catch {
      // 离线/弱网：忽略，下一轮或在回到前台时再查
    }
  }

  function start() {
    if (started || !import.meta.env.PROD) return
    started = true
    baseline = currentEntryFromDom()
    if (!baseline) return
    timer = window.setInterval(checkNow, CHECK_INTERVAL_MS)
    visibilityHandler = () => {
      if (document.visibilityState === 'visible') checkNow()
    }
    document.addEventListener('visibilitychange', visibilityHandler)
  }

  function stop() {
    started = false
    if (timer != null) {
      window.clearInterval(timer)
      timer = null
    }
    if (visibilityHandler) {
      document.removeEventListener('visibilitychange', visibilityHandler)
      visibilityHandler = null
    }
  }

  function reload() {
    window.location.reload()
  }

  return { available, checkNow, start, stop, reload }
})
