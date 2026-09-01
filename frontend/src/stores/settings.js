/**
 * Settings store.
 * Manages user preferences (notifications, privacy, display)
 * and the UI theme (light / dark / system).
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'

export const useSettingsStore = defineStore('settings', () => {
  const preferences = ref(null)
  const loading = ref(false)
  const error = ref(null)

  /* ── 主题状态（'light' | 'dark' | 'system'，默认跟随系统）── */
  const theme = ref('system')
  const systemPrefersDark = ref(false)

  let mediaQuery = null
  let mediaListener = null

  const resolvedTheme = computed(() => {
    if (theme.value === 'system') {
      return systemPrefersDark.value ? 'dark' : 'light'
    }
    return theme.value
  })

  function applyTheme() {
    if (typeof document === 'undefined') {
      return
    }
    const dark = resolvedTheme.value === 'dark'
    document.documentElement.classList.toggle('dark', dark)
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  }

  /** 读 localStorage + matchMedia 监听 + html.classList 切换（App 挂载前调用） */
  function initTheme() {
    if (typeof window === 'undefined') {
      return
    }

    try {
      const stored = window.localStorage.getItem('theme')
      if (stored === 'light' || stored === 'dark' || stored === 'system') {
        theme.value = stored
      }
    } catch {
      /* localStorage 不可用时保持 system */
    }

    if (window.matchMedia) {
      mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
      systemPrefersDark.value = mediaQuery.matches
      mediaListener = (event) => {
        systemPrefersDark.value = event.matches
        if (theme.value === 'system') {
          applyTheme()
        }
      }
      if (typeof mediaQuery.addEventListener === 'function') {
        mediaQuery.addEventListener('change', mediaListener)
      } else if (typeof mediaQuery.addListener === 'function') {
        mediaQuery.addListener(mediaListener)
      }
    }

    applyTheme()
  }

  /** 设置主题并持久化（即时生效） */
  function setTheme(next) {
    if (!['light', 'dark', 'system'].includes(next)) {
      return
    }
    theme.value = next
    try {
      window.localStorage.setItem('theme', next)
    } catch {
      /* 忽略持久化失败 */
    }
    applyTheme()
  }

  function resetThemeListener() {
    if (mediaQuery && mediaListener) {
      if (typeof mediaQuery.removeEventListener === 'function') {
        mediaQuery.removeEventListener('change', mediaListener)
      } else if (typeof mediaQuery.removeListener === 'function') {
        mediaQuery.removeListener(mediaListener)
      }
    }
    mediaQuery = null
    mediaListener = null
  }

  async function fetchPreferences() {
    loading.value = true
    error.value = null

    try {
      const response = await api.get('/notifications/settings')
      preferences.value = response.data
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function updatePreferences(updates) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put('/notifications/settings', updates)
      preferences.value = response.data
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  function resetState() {
    preferences.value = null
    error.value = null
  }

  return {
    preferences,
    loading,
    error,
    fetchPreferences,
    updatePreferences,
    resetState,
    // 主题
    theme,
    resolvedTheme,
    systemPrefersDark,
    initTheme,
    setTheme,
    resetThemeListener,
  }
})
