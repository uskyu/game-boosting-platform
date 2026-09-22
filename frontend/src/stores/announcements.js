import { defineStore } from 'pinia'
import { ref } from 'vue'

import api from '@/utils/api'

export const useAnnouncementsStore = defineStore('announcements', () => {
  const active = ref(null)
  const loading = ref(false)
  const error = ref('')
  const shownIds = new Set()

  async function fetchActive() {
    if (loading.value) return { success: false, error: 'loading' }
    loading.value = true
    error.value = ''
    try {
      const response = await api.get('/announcements/active')
      active.value = response.data || null
      return { success: true, data: active.value }
    } catch (err) {
      error.value = err.message || '公告加载失败'
      active.value = null
      return { success: false, error: error.value }
    } finally {
      loading.value = false
    }
  }

  async function markShown(announcementId) {
    if (!announcementId || shownIds.has(announcementId)) return
    shownIds.add(announcementId)
    try {
      await api.post(`/announcements/${announcementId}/shown`)
    } catch {
      // Closing the announcement should not trap the user when the tracking call fails.
    }
  }

  function closeActive() {
    const announcementId = active.value?.id
    if (announcementId) markShown(announcementId)
    active.value = null
  }

  function resetState() {
    active.value = null
    loading.value = false
    error.value = ''
    shownIds.clear()
  }

  return { active, loading, error, fetchActive, markShown, closeActive, resetState }
})
