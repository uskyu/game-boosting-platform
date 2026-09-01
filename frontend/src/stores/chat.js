import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'
import { useAuthStore } from '@/stores/auth'

const DEFAULT_MESSAGE_LIMIT = 30
const MAX_RECONNECT_DELAY = 30000
const HEARTBEAT_INTERVAL = 30000
const TYPING_THROTTLE_MS = 3000
const TYPING_VISIBLE_MS = 5000

function sortConversations(items = []) {
  return [...items].sort((left, right) => {
    const leftTime = new Date(left.last_message_at || left.updated_at || left.created_at || 0).getTime()
    const rightTime = new Date(right.last_message_at || right.updated_at || right.created_at || 0).getTime()
    return rightTime - leftTime
  })
}

function buildMessagePreview(message) {
  if (!message) {
    return ''
  }

  if (message.recalled_at) {
    return '消息已撤回'
  }

  if (message.message_type === 'IMAGE') {
    return '[图片]'
  }

  return message.content || ''
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref([])
  const activeConversationId = ref(null)
  const messagesByConversation = ref({})
  const unreadTotal = ref(0)
  const unreadByConversation = ref({})
  const socket = ref(null)
  const socketStatus = ref('disconnected')
  const typingUsers = ref({})
  const loading = ref(false)
  const hasMore = ref({})
  const error = ref(null)

  let reconnectTimer = null
  let heartbeatTimer = null
  let reconnectAttempts = 0
  let shouldReconnect = true
  const typingHideTimers = {}
  const lastTypingSentAt = {}

  const activeConversation = computed(() => (
    conversations.value.find((item) => item.id === activeConversationId.value) || null
  ))

  const currentMessages = computed(() => (
    messagesByConversation.value[activeConversationId.value] || []
  ))

  function syncUnreadTotal() {
    unreadTotal.value = Object.values(unreadByConversation.value)
      .reduce((sum, value) => sum + Number(value || 0), 0)
  }

  function ensureMessageBucket(conversationId) {
    if (!messagesByConversation.value[conversationId]) {
      messagesByConversation.value = {
        ...messagesByConversation.value,
        [conversationId]: [],
      }
    }
  }

  function replaceConversation(nextConversation) {
    const index = conversations.value.findIndex((item) => item.id === nextConversation.id)
    const mergedConversation = index === -1
      ? nextConversation
      : {
          ...conversations.value[index],
          ...nextConversation,
          participants: nextConversation.participants || conversations.value[index].participants || [],
          other_participants: nextConversation.other_participants || conversations.value[index].other_participants || [],
          order: nextConversation.order || conversations.value[index].order || null,
        }

    if (index === -1) {
      conversations.value = sortConversations([...conversations.value, mergedConversation])
    } else {
      const nextItems = [...conversations.value]
      nextItems[index] = mergedConversation
      conversations.value = sortConversations(nextItems)
    }

    unreadByConversation.value = {
      ...unreadByConversation.value,
      [mergedConversation.id]: Number(mergedConversation.unread_count || 0),
    }
    syncUnreadTotal()

    return mergedConversation
  }

  function findMessageConversation(messageId) {
    const entry = Object.entries(messagesByConversation.value)
      .find(([, items]) => items.some((message) => message.id === messageId))

    return entry ? Number(entry[0]) : null
  }

  function mergeMessages(conversationId, incomingMessages = []) {
    ensureMessageBucket(conversationId)

    const existing = messagesByConversation.value[conversationId] || []
    const mergedMap = new Map(existing.map((message) => [message.id, message]))

    incomingMessages.forEach((message) => {
      mergedMap.set(message.id, {
        ...mergedMap.get(message.id),
        ...message,
      })
    })

    const merged = [...mergedMap.values()].sort((left, right) => left.id - right.id)
    messagesByConversation.value = {
      ...messagesByConversation.value,
      [conversationId]: merged,
    }

    return merged
  }

  function updateConversationPreview(conversationId, message) {
    const conversation = conversations.value.find((item) => item.id === conversationId)
    if (!conversation) {
      return
    }

    replaceConversation({
      ...conversation,
      last_message_at: message?.created_at || conversation.last_message_at,
      last_message_preview: buildMessagePreview(message),
      unread_count: unreadByConversation.value[conversationId] || 0,
    })
  }

  function updateConversationPreviewFromMessages(conversationId) {
    const messages = messagesByConversation.value[conversationId] || []
    const latestMessage = messages[messages.length - 1]
    if (latestMessage) {
      updateConversationPreview(conversationId, latestMessage)
    }
  }

  function appendMessage(conversationId, message) {
    mergeMessages(conversationId, [message])
    updateConversationPreview(conversationId, message)
  }

  function updateParticipantReadState(conversationId, userId, lastReadMessageId) {
    const index = conversations.value.findIndex((item) => item.id === conversationId)
    if (index === -1) {
      return
    }

    const conversation = conversations.value[index]
    const participants = (conversation.participants || []).map((participant) => {
      if (participant.user_id !== userId) {
        return participant
      }

      return {
        ...participant,
        last_read_message_id: Math.max(
          Number(participant.last_read_message_id || 0),
          Number(lastReadMessageId || 0)
        ) || null,
        last_read_at: new Date().toISOString(),
      }
    })

    replaceConversation({
      ...conversation,
      participants,
    })
  }

  function markConversationLocallyRead(conversationId, messageId) {
    const previousUnread = Number(unreadByConversation.value[conversationId] || 0)
    unreadByConversation.value = {
      ...unreadByConversation.value,
      [conversationId]: 0,
    }

    if (previousUnread > 0) {
      unreadTotal.value = Math.max(0, unreadTotal.value - previousUnread)
    }

    const authStore = useAuthStore()
    updateParticipantReadState(conversationId, authStore.user?.id, messageId)

    const conversation = conversations.value.find((item) => item.id === conversationId)
    if (conversation) {
      replaceConversation({
        ...conversation,
        unread_count: 0,
      })
    }
  }

  function incrementConversationUnread(conversationId) {
    const nextCount = Number(unreadByConversation.value[conversationId] || 0) + 1
    unreadByConversation.value = {
      ...unreadByConversation.value,
      [conversationId]: nextCount,
    }
    syncUnreadTotal()

    const conversation = conversations.value.find((item) => item.id === conversationId)
    if (conversation) {
      replaceConversation({
        ...conversation,
        unread_count: nextCount,
      })
    }
  }

  function addAdminParticipant(conversationId, adminUser) {
    const index = conversations.value.findIndex((item) => item.id === conversationId)
    if (index === -1 || !adminUser) {
      return
    }

    const conversation = conversations.value[index]
    const participantExists = (conversation.participants || [])
      .some((participant) => participant.user_id === adminUser.id)

    if (!participantExists) {
      conversation.participants = [
        ...(conversation.participants || []),
        {
          id: Date.now(),
          user_id: adminUser.id,
          role_snapshot: adminUser.role,
          joined_at: new Date().toISOString(),
          last_read_message_id: null,
          last_read_at: null,
          user: adminUser,
        },
      ]
    }

    const otherParticipantExists = (conversation.other_participants || [])
      .some((participant) => participant.id === adminUser.id)

    if (!otherParticipantExists) {
      conversation.other_participants = [
        ...(conversation.other_participants || []),
        adminUser,
      ]
    }

    replaceConversation({ ...conversation })
  }

  function clearReconnectTimer() {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function clearHeartbeat() {
    if (heartbeatTimer) {
      window.clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function clearTypingState() {
    Object.values(typingHideTimers).forEach((timer) => window.clearTimeout(timer))
    Object.keys(typingHideTimers).forEach((key) => {
      delete typingHideTimers[key]
    })
    typingUsers.value = {}
  }

  function resetState() {
    conversations.value = []
    activeConversationId.value = null
    messagesByConversation.value = {}
    unreadTotal.value = 0
    unreadByConversation.value = {}
    typingUsers.value = {}
    hasMore.value = {}
    error.value = null
    clearTypingState()
    Object.keys(lastTypingSentAt).forEach((key) => {
      delete lastTypingSentAt[key]
    })
  }

  function setTypingUser(conversationId, userId) {
    if (typingHideTimers[conversationId]) {
      window.clearTimeout(typingHideTimers[conversationId])
    }

    typingUsers.value = {
      ...typingUsers.value,
      [conversationId]: { userId },
    }

    typingHideTimers[conversationId] = window.setTimeout(() => {
      const nextTypingUsers = { ...typingUsers.value }
      delete nextTypingUsers[conversationId]
      typingUsers.value = nextTypingUsers
      delete typingHideTimers[conversationId]
    }, TYPING_VISIBLE_MS)
  }

  function getWebSocketUrl() {
    return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/chat/ws`
  }

  function scheduleReconnect() {
    clearReconnectTimer()

    const authStore = useAuthStore()
    if (!shouldReconnect || !authStore.accessToken) {
      return
    }

    const delay = Math.min(1000 * (2 ** reconnectAttempts), MAX_RECONNECT_DELAY)
    reconnectAttempts += 1

    reconnectTimer = window.setTimeout(() => {
      connectWebSocket()
    }, delay)
  }

  function startHeartbeat() {
    clearHeartbeat()
    heartbeatTimer = window.setInterval(() => {
      if (socket.value?.readyState === WebSocket.OPEN) {
        socket.value.send(JSON.stringify({ event: 'ping', data: {} }))
      }
    }, HEARTBEAT_INTERVAL)
  }

  async function fetchConversations(options = {}) {
    loading.value = true
    error.value = null

    const params = {
      page: options.page || 1,
      page_size: options.pageSize || 20,
    }

    try {
      const response = await api.get('/chat/conversations', { params })
      conversations.value = sortConversations(response.data.items || [])

      const nextUnreadMap = {}
      conversations.value.forEach((conversation) => {
        nextUnreadMap[conversation.id] = Number(conversation.unread_count || 0)
      })
      unreadByConversation.value = nextUnreadMap
      syncUnreadTotal()

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function fetchConversation(conversationId) {
    if (!conversationId) {
      return { success: false, error: '无效的会话 ID' }
    }

    try {
      const response = await api.get(`/chat/conversations/${conversationId}`)
      const conversation = replaceConversation(response.data)
      return { success: true, data: conversation }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function fetchMessages(conversationId, beforeId = null, limit = DEFAULT_MESSAGE_LIMIT) {
    if (!conversationId) {
      return { success: false, error: '无效的会话 ID' }
    }

    ensureMessageBucket(conversationId)

    try {
      const response = await api.get(`/chat/conversations/${conversationId}/messages`, {
        params: {
          before_id: beforeId || undefined,
          limit,
        },
      })

      const messages = Array.isArray(response.data) ? response.data : []
      if (beforeId) {
        mergeMessages(conversationId, messages)
      } else {
        messagesByConversation.value = {
          ...messagesByConversation.value,
          [conversationId]: [...messages].sort((left, right) => left.id - right.id),
        }
      }

      hasMore.value = {
        ...hasMore.value,
        [conversationId]: messages.length >= limit,
      }

      if (!beforeId && messages.length > 0) {
        const latestMessage = messages[messages.length - 1]
        await markRead(conversationId, latestMessage.id)
      }

      return { success: true, data: messages }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function searchMessages(conversationId, query, limit = 20) {
    const keyword = query?.trim()
    if (!keyword) {
      return { success: true, data: [] }
    }

    try {
      const response = await api.get(`/chat/conversations/${conversationId}/messages/search`, {
        params: {
          q: keyword,
          limit,
        },
      })
      return { success: true, data: response.data || [] }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function sendMessage(conversationId, content) {
    const messageContent = content?.trim()
    if (!messageContent) {
      return { success: false, error: '消息内容不能为空' }
    }

    try {
      const response = await api.post(`/chat/conversations/${conversationId}/messages`, {
        content: messageContent,
      })
      appendMessage(conversationId, response.data)
      markConversationLocallyRead(conversationId, response.data.id)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function sendImage(conversationId, file) {
    if (!file) {
      return { success: false, error: '请选择图片' }
    }

    const formData = new FormData()
    formData.append('file', file)

    try {
      const response = await api.post(
        `/chat/conversations/${conversationId}/upload`,
        formData,
        {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        }
      )
      appendMessage(conversationId, response.data)
      markConversationLocallyRead(conversationId, response.data.id)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function startConversation(targetUserId, orderId = null) {
    try {
      const response = await api.post('/chat/conversations', {
        target_user_id: targetUserId,
        order_id: orderId,
      })
      const conversation = replaceConversation(response.data)
      return { success: true, data: conversation }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function markRead(conversationId, messageId = null) {
    try {
      await api.post(`/chat/conversations/${conversationId}/read`, {
        last_read_message_id: messageId,
      })
      markConversationLocallyRead(conversationId, messageId)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function recallMessage(messageId) {
    const conversationId = findMessageConversation(messageId)

    try {
      await api.post(`/chat/messages/${messageId}/recall`)

      if (conversationId) {
        const nextMessages = (messagesByConversation.value[conversationId] || []).map((message) => (
          message.id === messageId
            ? {
                ...message,
                recalled_at: new Date().toISOString(),
                content: null,
              }
            : message
        ))

        messagesByConversation.value = {
          ...messagesByConversation.value,
          [conversationId]: nextMessages,
        }
        updateConversationPreviewFromMessages(conversationId)
      }

      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function deleteMessage(messageId) {
    const conversationId = findMessageConversation(messageId)

    try {
      await api.delete(`/chat/messages/${messageId}`)

      if (conversationId) {
        const nextMessages = (messagesByConversation.value[conversationId] || [])
          .filter((message) => message.id !== messageId)

        messagesByConversation.value = {
          ...messagesByConversation.value,
          [conversationId]: nextMessages,
        }
        updateConversationPreviewFromMessages(conversationId)
      }

      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function inviteAdmin(conversationId) {
    try {
      const response = await api.post(`/chat/conversations/${conversationId}/invite-admin`)
      addAdminParticipant(conversationId, response.data.admin)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function fetchUnreadSummary() {
    try {
      const response = await api.get('/chat/unread-summary')
      unreadTotal.value = Number(response.data.total_unread || 0)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  function setActiveConversation(conversationId) {
    activeConversationId.value = conversationId ? Number(conversationId) : null
  }

  function sendTyping(conversationId) {
    if (!conversationId || socketStatus.value !== 'connected' || socket.value?.readyState !== WebSocket.OPEN) {
      return
    }

    const now = Date.now()
    if (
      lastTypingSentAt[conversationId]
      && now - lastTypingSentAt[conversationId] < TYPING_THROTTLE_MS
    ) {
      return
    }

    lastTypingSentAt[conversationId] = now
    socket.value.send(JSON.stringify({
      event: 'typing',
      data: { conversation_id: Number(conversationId) },
    }))
  }

  function connectWebSocket() {
    const authStore = useAuthStore()
    const token = authStore.accessToken

    if (!token) {
      return
    }

    if (socket.value && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.value.readyState)) {
      return
    }

    shouldReconnect = true
    clearReconnectTimer()
    socketStatus.value = 'connecting'

    const nextSocket = new WebSocket(getWebSocketUrl())
    socket.value = nextSocket

    nextSocket.onopen = () => {
      if (socket.value !== nextSocket) {
        return
      }

      // Send auth event as first message (token is NOT in the URL)
      nextSocket.send(JSON.stringify({
        event: 'auth',
        data: { token },
      }))
    }

    nextSocket.onmessage = (event) => {
      let parsed
      try {
        parsed = JSON.parse(event.data)
      } catch {
        return
      }

      // Handle auth handshake responses before normal message loop
      if (parsed.event === 'auth_ok') {
        reconnectAttempts = 0
        socketStatus.value = 'connected'
        startHeartbeat()
        fetchUnreadSummary()
        if (conversations.value.length > 0) {
          fetchConversations({
            page: 1,
            pageSize: Math.max(conversations.value.length, 20),
          })
        }
        return
      }

      if (parsed.event === 'auth_fail') {
        shouldReconnect = false
        nextSocket.close()
        return
      }

      handleWsMessage(event).catch((err) => {
        // eslint-disable-next-line no-console
        console.error('[chat] ws message handler error:', err)
      })
    }

    nextSocket.onerror = () => {
      nextSocket.close()
    }

    nextSocket.onclose = () => {
      if (socket.value === nextSocket) {
        socket.value = null
      }

      clearHeartbeat()
      socketStatus.value = 'disconnected'

      if (shouldReconnect && useAuthStore().accessToken) {
        scheduleReconnect()
      }
    }
  }

  function disconnectWebSocket(options = {}) {
    shouldReconnect = false
    clearReconnectTimer()
    clearHeartbeat()

    if (socket.value) {
      const currentSocket = socket.value
      socket.value = null
      currentSocket.close()
    }

    socketStatus.value = 'disconnected'

    if (options.clearState) {
      resetState()
    }
  }

  async function handleWsMessage(event) {
    let payload = null

    try {
      payload = typeof event.data === 'string'
        ? JSON.parse(event.data)
        : event.data
    } catch (parseError) {
      // silently ignore unparseable frames
      return
    }

    if (!payload?.event) {
      return
    }

    const authStore = useAuthStore()
    const currentUserId = authStore.user?.id
    const data = payload.data || {}

    switch (payload.event) {
      case 'new_message': {
        const message = data.message
        const conversationId = Number(data.conversation_id || message?.conversation_id || 0)

        if (!conversationId || !message) {
          return
        }

        if (!conversations.value.some((item) => item.id === conversationId)) {
          await fetchConversation(conversationId)
        }

        appendMessage(conversationId, message)

        if (message.sender_id !== currentUserId) {
          if (activeConversationId.value === conversationId) {
            await markRead(conversationId, message.id)
          } else {
            incrementConversationUnread(conversationId)
          }
        }
        break
      }

      case 'message_recalled': {
        const conversationId = Number(data.conversation_id || 0)
        const messageId = Number(data.message_id || 0)
        if (!conversationId || !messageId) {
          return
        }

        const nextMessages = (messagesByConversation.value[conversationId] || []).map((message) => (
          message.id === messageId
            ? {
                ...message,
                recalled_at: message.recalled_at || new Date().toISOString(),
                content: null,
              }
            : message
        ))

        messagesByConversation.value = {
          ...messagesByConversation.value,
          [conversationId]: nextMessages,
        }
        updateConversationPreviewFromMessages(conversationId)
        break
      }

      case 'message_read': {
        updateParticipantReadState(
          Number(data.conversation_id || 0),
          Number(data.user_id || 0),
          Number(data.last_read_message_id || 0)
        )
        break
      }

      case 'admin_joined': {
        const conversationId = Number(data.conversation_id || 0)
        if (!conversationId) {
          return
        }

        if (!conversations.value.some((item) => item.id === conversationId)) {
          await fetchConversation(conversationId)
        } else {
          addAdminParticipant(conversationId, data.admin)
        }
        break
      }

      case 'typing': {
        const conversationId = Number(data.conversation_id || 0)
        const userId = Number(data.user_id || 0)

        if (!conversationId || !userId || userId === currentUserId) {
          return
        }

        setTypingUser(conversationId, userId)
        break
      }

      case 'notification': {
        // Delegate to the notifications store for real-time badge update
        try {
          const { useNotificationsStore } = await import('@/stores/notifications')
          const notificationsStore = useNotificationsStore()
          notificationsStore.handleRealtimeNotification(data)
        } catch {
          // notifications store may not be loaded yet – ignore
        }
        break
      }

      case 'pong':
        break

      default:
        // unknown event – ignore
    }
  }

  return {
    conversations,
    activeConversationId,
    messagesByConversation,
    unreadTotal,
    unreadByConversation,
    socket,
    socketStatus,
    typingUsers,
    loading,
    hasMore,
    error,
    activeConversation,
    currentMessages,
    fetchConversations,
    fetchConversation,
    fetchMessages,
    searchMessages,
    sendMessage,
    sendImage,
    startConversation,
    markRead,
    recallMessage,
    deleteMessage,
    inviteAdmin,
    fetchUnreadSummary,
    setActiveConversation,
    sendTyping,
    connectWebSocket,
    disconnectWebSocket,
    handleWsMessage,
    resetState,
  }
})
