/**
 * Notification store.
 * Manages notification list, unread count, and real-time updates.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'

export const useNotificationsStore = defineStore('notifications', () => {
  const notifications = ref([])
  const total = ref(0)
  const unreadCount = ref(0)
  const loading = ref(false)
  const error = ref(null)

  const hasUnread = computed(() => unreadCount.value > 0)

  async function fetchNotifications(options = {}) {
    loading.value = true
    error.value = null

    const params = {
      page: options.page || 1,
      page_size: options.pageSize || 20,
      unread_only: options.unreadOnly || false,
    }

    try {
      const response = await api.get('/notifications', { params })
      notifications.value = response.data.items || []
      total.value = response.data.total || 0
      unreadCount.value = response.data.unread_count || 0
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function fetchUnreadCount() {
    try {
      const response = await api.get('/notifications/unread-count')
      unreadCount.value = response.data.count || 0
      return { success: true }
    } catch (err) {
      return { success: false, error: err.message }
    }
  }

  async function markRead(notificationId) {
    try {
      await api.post(`/notifications/${notificationId}/read`)
      const item = notifications.value.find((n) => n.id === notificationId)
      if (item && !item.is_read) {
        item.is_read = true
        item.read_at = new Date().toISOString()
        unreadCount.value = Math.max(0, unreadCount.value - 1)
      }
      return { success: true }
    } catch (err) {
      return { success: false, error: err.message }
    }
  }

  async function markAllRead() {
    try {
      await api.post('/notifications/read-all')
      notifications.value.forEach((n) => {
        n.is_read = true
        n.read_at = n.read_at || new Date().toISOString()
      })
      unreadCount.value = 0
      return { success: true }
    } catch (err) {
      return { success: false, error: err.message }
    }
  }

  function handleRealtimeNotification(payload) {
    notifications.value.unshift(payload)
    unreadCount.value += 1
    total.value += 1
  }

  function resetState() {
    notifications.value = []
    total.value = 0
    unreadCount.value = 0
    error.value = null
  }

  return {
    notifications,
    total,
    unreadCount,
    loading,
    error,
    hasUnread,
    fetchNotifications,
    fetchUnreadCount,
    markRead,
    markAllRead,
    handleRealtimeNotification,
    resetState,
  }
})
