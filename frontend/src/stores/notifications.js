/**
 * Notification store.
 * Manages notification list, unread count, and real-time updates.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'
import { useSettingsStore } from '@/stores/settings'
import { useToastsStore } from '@/stores/toasts'

export const useNotificationsStore = defineStore('notifications', () => {
  const notifications = ref([])
  const total = ref(0)
  const unreadCount = ref(0)
  const loading = ref(false)
  const error = ref(null)
  const receivedIds = new Set()

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
      notifications.value.forEach((item) => receivedIds.add(String(item.id)))
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

  function announceOrderNotification(payload) {
    if (payload?.id == null) return
    const settings = useSettingsStore().preferences?.notification_settings || {}
    if (settings.global_dnd === true || settings[payload.type] === false) return
    const sound = payload.type === 'ORDER_ACCEPTED'
      ? 'claimed'
      : payload.type === 'SYSTEM_ANNOUNCEMENT' && String(payload.title || '').includes('新订单')
        ? 'new-order'
        : null
    if (!sound) return
    useToastsStore().pushToast({
      title: payload.title,
      body: payload.content || '',
      to: payload.link || null,
      sound,
      dedupKey: `notif-${payload.id}`,
    })
  }

  function handleRealtimeNotification(payload) {
    if (payload?.id == null) return false
    const id = String(payload.id)
    if (receivedIds.has(id) || notifications.value.some((item) => String(item.id) === id)) {
      return false
    }
    receivedIds.add(id)
    notifications.value.unshift(payload)
    if (!payload.is_read) unreadCount.value += 1
    total.value += 1
    return true
  }

  function resetState() {
    receivedIds.clear()
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
    announceOrderNotification,
    resetState,
  }
})
