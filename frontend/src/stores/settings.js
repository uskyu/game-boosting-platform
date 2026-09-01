/**
 * Settings store.
 * Manages user preferences (notifications, privacy, display).
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'

export const useSettingsStore = defineStore('settings', () => {
  const preferences = ref(null)
  const loading = ref(false)
  const error = ref(null)

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
  }
})
