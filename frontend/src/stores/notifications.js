/**
 * Notification store.
 * Manages notification list, unread count, and real-time updates.
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import { useToastsStore } from '@/stores/toasts'
import { useOrdersStore } from '@/stores/orders'

export const useNotificationsStore = defineStore('notifications', () => {
  const notifications = ref([])
  const total = ref(0)
  const unreadCount = ref(0)
  const loading = ref(false)
  const error = ref(null)
  // Monotonic signal for views that need to react to a newly received order.
  const newOrderNotificationVersion = ref(0)
  const receivedIds = new Set()
  const signaledNewOrderIds = new Set()
  const pendingRealtimeNotifications = new Map()
  let listRequestSeq = 0
  let unreadRequestSeq = 0
  let mutationRequestSeq = 0
  let resetEpoch = 0

  const hasUnread = computed(() => unreadCount.value > 0)
  const authStore = useAuthStore()

  function captureRequestContext() {
    return {
      session: authStore.getSessionContext(),
      epoch: resetEpoch,
    }
  }

  function isCurrentRequest(context, options = {}) {
    return context
      && context.epoch === resetEpoch
      && authStore.isCurrentSession(context.session)
      && (typeof options.isCurrent !== 'function' || options.isCurrent())
  }

  function isNewOrderNotification(item) {
    return item?.type === 'SYSTEM_ANNOUNCEMENT' && String(item.title || '').includes('新订单')
  }

  function mergeNotifications(items, page = 1) {
    const fetched = Array.isArray(items) ? items : []
    const fetchedIds = new Set(fetched.map((item) => String(item.id)))
    const realtimeExtras = page === 1
      ? [...pendingRealtimeNotifications.values()].filter((item) => !fetchedIds.has(String(item.id)))
      : []
    const merged = [...fetched, ...realtimeExtras]
    pendingRealtimeNotifications.forEach((item, id) => {
      if (fetchedIds.has(id)) pendingRealtimeNotifications.delete(id)
    })
    const deduped = []
    const seen = new Set()
    merged.forEach((item) => {
      const id = String(item?.id)
      if (!item || seen.has(id)) return
      seen.add(id)
      deduped.push(item)
    })
    return deduped
  }

  async function fetchNotifications(options = {}) {
    const context = captureRequestContext()
    const requestSeq = ++listRequestSeq
    loading.value = true
    error.value = null

    const params = {
      page: options.page || 1,
      page_size: options.pageSize || 20,
      unread_only: options.unreadOnly || false,
    }

    try {
      const response = await api.get('/notifications', { params })
      if (requestSeq !== listRequestSeq || !isCurrentRequest(context)) {
        return { success: true, stale: true }
      }
      const fetchedItems = response.data.items || []
      const previousIds = new Set(notifications.value.map((item) => String(item.id)))
      const mergedItems = mergeNotifications(fetchedItems, params.page)
      notifications.value = mergedItems
      mergedItems.forEach((item) => receivedIds.add(String(item.id)))
      // The first page is ordered newest-first. Signal only genuinely new
      // entries there, regardless of whether list hydration has completed;
      // realtime arrivals may race this request and already signal themselves.
      if (params.page === 1) {
        mergedItems.forEach((item) => {
          if (!previousIds.has(String(item.id))) signalNewOrderNotification(item)
        })
      }
      total.value = response.data.total || 0
      unreadCount.value = response.data.unread_count || 0
      return { success: true, data: { ...response.data, items: mergedItems } }
    } catch (err) {
      if (!isCurrentRequest(context)) return { success: false, stale: true, error: err.message }
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      if (requestSeq === listRequestSeq) loading.value = false
    }
  }

  async function fetchUnreadCount(options = {}) {
    const context = captureRequestContext()
    const requestSeq = ++unreadRequestSeq
    try {
      const response = await api.get('/notifications/unread-count')
      if (requestSeq !== unreadRequestSeq || !isCurrentRequest(context, options)) {
        return { success: true, stale: true }
      }
      unreadCount.value = response.data.count || 0
      return { success: true }
    } catch (err) {
      return isCurrentRequest(context, options)
        ? { success: false, error: err.message }
        : { success: false, stale: true, error: err.message }
    }
  }

  async function markRead(notificationId) {
    const context = captureRequestContext()
    const requestSeq = ++mutationRequestSeq
    try {
      await api.post(`/notifications/${notificationId}/read`)
      if (requestSeq !== mutationRequestSeq || !isCurrentRequest(context)) return { success: true, stale: true }
      const item = notifications.value.find((n) => n.id === notificationId)
      if (item && !item.is_read) {
        item.is_read = true
        item.read_at = new Date().toISOString()
        unreadCount.value = Math.max(0, unreadCount.value - 1)
      }
      return { success: true }
    } catch (err) {
      return isCurrentRequest(context) ? { success: false, error: err.message } : { success: false, stale: true, error: err.message }
    }
  }

  async function markAllRead() {
    const context = captureRequestContext()
    const requestSeq = ++mutationRequestSeq
    try {
      await api.post('/notifications/read-all')
      if (requestSeq !== mutationRequestSeq || !isCurrentRequest(context)) return { success: true, stale: true }
      notifications.value.forEach((n) => {
        n.is_read = true
        n.read_at = n.read_at || new Date().toISOString()
      })
      unreadCount.value = 0
      return { success: true }
    } catch (err) {
      return isCurrentRequest(context) ? { success: false, error: err.message } : { success: false, stale: true, error: err.message }
    }
  }

  function announceOrderNotification(payload, options = {}) {
    if (payload?.id == null || !authStore.isAuthenticated) return
    if (typeof options.isCurrent === 'function' && !options.isCurrent()) return
    const settings = useSettingsStore().preferences?.notification_settings || {}
    if (settings.global_dnd === true || settings[payload.type] === false) return
    // 订单被申请取消：播「有订单申请取消了请及时查看」（与来单/被接手同级的独立提示音）
    const sound = payload.type === 'ORDER_CANCEL_REQUESTED'
      ? 'cancel-requested'
      : payload.type === 'ORDER_ACCEPTED'
        ? 'claimed'
        : isNewOrderNotification(payload)
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

  function signalNewOrderNotification(payload) {
    if (!isNewOrderNotification(payload) || payload?.id == null) return false
    const id = String(payload.id)
    if (signaledNewOrderIds.has(id)) return false
    signaledNewOrderIds.add(id)
    // 服务端直推大厅卡片：本地插卡即可让新单即时可见，无需 bump 版本触发
    // 大厅全量重刷（新单到达不再让 600 个客户端各自全量拉列表）。
    // 少数派（老服务器/不带 order_card 的来源）保持旧行为：bump → 大厅
    // silentRefresh 全量兜底。version 的唯一监听者是大厅 watch。
    if (payload.order_card) {
      useOrdersStore().applyNewOrderCard(payload.order_card)
      return true
    }
    newOrderNotificationVersion.value += 1
    return true
  }

  function handleRealtimeNotification(payload) {
    if (payload?.id == null || !authStore.isAuthenticated) return false
    signalNewOrderNotification(payload)
    const id = String(payload.id)
    if (receivedIds.has(id) || notifications.value.some((item) => String(item.id) === id)) {
      return false
    }
    receivedIds.add(id)
    pendingRealtimeNotifications.set(id, payload)
    notifications.value.unshift(payload)
    if (!payload.is_read) unreadCount.value += 1
    total.value += 1
    return true
  }

  function resetState() {
    resetEpoch += 1
    listRequestSeq += 1
    unreadRequestSeq += 1
    mutationRequestSeq += 1
    receivedIds.clear()
    pendingRealtimeNotifications.clear()
    signaledNewOrderIds.clear()
    notifications.value = []
    total.value = 0
    unreadCount.value = 0
    newOrderNotificationVersion.value = 0
    loading.value = false
    error.value = null
  }

  return {
    notifications,
    total,
    unreadCount,
    loading,
    error,
    newOrderNotificationVersion,
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
