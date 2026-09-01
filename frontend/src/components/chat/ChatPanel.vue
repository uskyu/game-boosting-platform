<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import ChatComposer from './ChatComposer.vue'
import ChatMessageList from './ChatMessageList.vue'
import ChatSearchBar from './ChatSearchBar.vue'

const props = defineProps({
  conversationId: {
    type: [Number, String],
    required: true,
  },
})

const authStore = useAuthStore()
const chatStore = useChatStore()

const draft = ref('')
const searchQuery = ref('')
const searchResults = ref([])
const searching = ref(false)
const hasSearched = ref(false)
const loadingHistory = ref(false)
const sending = ref(false)
const invitingAdmin = ref(false)
const actionMessage = ref('')
const actionError = ref('')
const highlightedMessageId = ref(null)
const messageListRef = ref(null)

let highlightTimer = null

const normalizedConversationId = computed(() => Number(props.conversationId))
const conversation = computed(() => (
  chatStore.conversations.find((item) => item.id === normalizedConversationId.value)
  || chatStore.activeConversation
))
const messages = computed(() => (
  chatStore.messagesByConversation[normalizedConversationId.value] || []
))
const currentUserId = computed(() => authStore.user?.id || null)
const typingUserId = computed(() => (
  chatStore.typingUsers[normalizedConversationId.value]?.userId || null
))
const hasMore = computed(() => Boolean(chatStore.hasMore[normalizedConversationId.value]))
const hasAdmin = computed(() => (
  conversation.value?.participants?.some(
    (participant) => participant.user?.role === 'ADMIN' || participant.role_snapshot === 'ADMIN'
  ) || false
))

const conversationTitle = computed(() => {
  const names = conversation.value?.other_participants
    ?.map((item) => item.username)
    .filter(Boolean)

  if (names?.length) {
    return names.join(' / ')
  }

  if (conversation.value?.order?.id) {
    return `订单 #${conversation.value.order.id}`
  }

  return '聊天会话'
})

const conversationSubtitle = computed(() => {
  if (conversation.value?.order) {
    return `${conversation.value.order.game_name} · ${conversation.value.order.current_rank} → ${conversation.value.order.target_rank}`
  }

  return '站内会话'
})

const socketStatusText = computed(() => {
  if (chatStore.socketStatus === 'connected') {
    return '在线'
  }

  if (chatStore.socketStatus === 'connecting') {
    return '重连中'
  }

  return '离线'
})

const readReceiptMessageId = computed(() => {
  if (!currentUserId.value || !conversation.value) {
    return null
  }

  const lastReadBoundary = Math.max(
    0,
    ...(conversation.value.participants || [])
      .filter((participant) => Number(participant.user_id) !== Number(currentUserId.value))
      .map((participant) => Number(participant.last_read_message_id || 0))
  )

  if (!lastReadBoundary) {
    return null
  }

  const ownMessages = messages.value
    .filter((message) => (
      Number(message.sender_id) === Number(currentUserId.value)
      && !message.recalled_at
      && message.message_type !== 'SYSTEM'
    ))

  const lastReadMessage = [...ownMessages]
    .reverse()
    .find((message) => Number(message.id) <= lastReadBoundary)

  return lastReadMessage?.id || null
})

function resetHighlight() {
  if (highlightTimer) {
    window.clearTimeout(highlightTimer)
    highlightTimer = null
  }

  highlightedMessageId.value = null
}

async function focusMessage(messageId) {
  if (!messageId) {
    return false
  }

  while (
    !messages.value.some((message) => Number(message.id) === Number(messageId))
    && hasMore.value
  ) {
    const oldestId = messages.value[0]?.id
    if (!oldestId) {
      break
    }

    const result = await chatStore.fetchMessages(normalizedConversationId.value, oldestId)
    if (!result.success || !result.data?.length) {
      break
    }
  }

  if (!messages.value.some((message) => Number(message.id) === Number(messageId))) {
    return false
  }

  highlightedMessageId.value = Number(messageId)
  await nextTick()
  messageListRef.value?.scrollToMessage(Number(messageId))

  highlightTimer = window.setTimeout(() => {
    highlightedMessageId.value = null
    highlightTimer = null
  }, 3000)

  return true
}

async function loadConversation() {
  actionError.value = ''
  actionMessage.value = ''
  resetHighlight()
  chatStore.setActiveConversation(normalizedConversationId.value)

  const detailResult = await chatStore.fetchConversation(normalizedConversationId.value)
  if (!detailResult.success) {
    actionError.value = detailResult.error || '加载会话失败'
    return
  }

  const messagesResult = await chatStore.fetchMessages(normalizedConversationId.value)
  if (!messagesResult.success) {
    actionError.value = messagesResult.error || '加载消息失败'
  }
}

async function handleLoadMore() {
  if (loadingHistory.value || !messages.value.length || !hasMore.value) {
    return
  }

  loadingHistory.value = true
  await chatStore.fetchMessages(normalizedConversationId.value, messages.value[0].id)
  loadingHistory.value = false
}

async function handleSend() {
  if (!draft.value.trim() || sending.value) {
    return
  }

  sending.value = true
  actionError.value = ''
  actionMessage.value = ''

  const result = await chatStore.sendMessage(normalizedConversationId.value, draft.value)
  if (result.success) {
    draft.value = ''
    await nextTick()
    messageListRef.value?.scrollToBottom(true)
  } else {
    actionError.value = result.error || '发送消息失败'
  }

  sending.value = false
}

async function handleSendImage(file) {
  if (!file || sending.value) {
    return
  }

  sending.value = true
  actionError.value = ''
  actionMessage.value = ''

  const result = await chatStore.sendImage(normalizedConversationId.value, file)
  if (result.success) {
    await nextTick()
    messageListRef.value?.scrollToBottom(true)
  } else {
    actionError.value = result.error || '发送图片失败'
  }

  sending.value = false
}

async function handleRecall(messageId) {
  actionError.value = ''
  const result = await chatStore.recallMessage(messageId)
  if (!result.success) {
    actionError.value = result.error || '撤回消息失败'
  }
}

async function handleSearch() {
  const keyword = searchQuery.value.trim()
  if (!keyword) {
    searchResults.value = []
    hasSearched.value = false
    return
  }

  searching.value = true
  actionError.value = ''
  hasSearched.value = true

  const result = await chatStore.searchMessages(normalizedConversationId.value, keyword)
  if (result.success) {
    searchResults.value = result.data || []
  } else {
    actionError.value = result.error || '搜索消息失败'
  }

  searching.value = false
}

function handleClearSearch() {
  searchResults.value = []
  hasSearched.value = false
  resetHighlight()
}

async function handleSelectSearchResult(message) {
  const focused = await focusMessage(message.id)
  if (!focused) {
    actionMessage.value = '该消息不在当前已加载范围内，请继续上滑加载历史记录后重试。'
    return
  }

  actionMessage.value = '已定位到搜索结果'
}

async function handleInviteAdmin() {
  if (hasAdmin.value || invitingAdmin.value) {
    return
  }

  invitingAdmin.value = true
  actionError.value = ''
  actionMessage.value = ''

  const result = await chatStore.inviteAdmin(normalizedConversationId.value)
  if (result.success) {
    await chatStore.fetchConversation(normalizedConversationId.value)
    await chatStore.fetchMessages(normalizedConversationId.value)
    actionMessage.value = result.data?.message || '已通知管理员介入当前会话'
    await nextTick()
    messageListRef.value?.scrollToBottom(true)
  } else {
    actionError.value = result.error || '邀请管理员失败'
  }

  invitingAdmin.value = false
}

watch(
  () => normalizedConversationId.value,
  async (conversationId) => {
    if (!conversationId) {
      return
    }

    searchQuery.value = ''
    searchResults.value = []
    hasSearched.value = false
    await loadConversation()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  resetHighlight()
  if (Number(chatStore.activeConversationId) === normalizedConversationId.value) {
    chatStore.setActiveConversation(null)
  }
})
</script>

<template>
  <section class="chat-shell">
    <div v-if="conversation" class="flex h-full flex-col">
      <header class="chat-panel-header">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-3">
            <h1 class="truncate text-2xl font-semibold text-white">{{ conversationTitle }}</h1>
            <span class="chat-status-pill">{{ socketStatusText }}</span>
          </div>
          <p class="mt-2 text-sm text-slate-400">{{ conversationSubtitle }}</p>
        </div>

        <button
          v-if="!hasAdmin && !authStore.isAdmin"
          type="button"
          class="btn-secondary shrink-0 !px-5"
          :disabled="invitingAdmin"
          @click="handleInviteAdmin"
        >
          {{ invitingAdmin ? '呼叫中...' : '呼叫客服' }}
        </button>
        <span
          v-else-if="hasAdmin"
          class="chat-status-pill border-accent-400/25 bg-accent-500/10 text-accent-200"
        >
          客服已加入
        </span>
      </header>

      <div class="border-b border-line-soft px-6 py-4 sm:px-8">
        <ChatSearchBar
          v-model="searchQuery"
          :results="searchResults"
          :loading="searching"
          :has-searched="hasSearched"
          @search="handleSearch"
          @clear="handleClearSearch"
          @select="handleSelectSearchResult"
        />
      </div>

      <div
        v-if="actionError || actionMessage"
        class="border-b border-line-soft px-6 py-3 sm:px-8"
      >
        <div v-if="actionError" class="message-error">
          {{ actionError }}
        </div>
        <div v-else-if="actionMessage" class="message-info">
          {{ actionMessage }}
        </div>
      </div>

      <div class="min-h-0 flex-1 px-6 py-6 sm:px-8">
        <ChatMessageList
          ref="messageListRef"
          :messages="messages"
          :conversation="conversation"
          :current-user-id="currentUserId"
          :has-more="hasMore"
          :loading-more="loadingHistory"
          :typing-user-id="typingUserId"
          :read-receipt-message-id="readReceiptMessageId"
          :highlighted-message-id="highlightedMessageId"
          @load-more="handleLoadMore"
          @recall="handleRecall"
        />
      </div>

      <div class="border-t border-line-soft px-6 py-5 sm:px-8">
        <ChatComposer
          v-model="draft"
          :disabled="!conversation"
          :sending="sending"
          @send="handleSend"
          @send-image="handleSendImage"
          @typing="chatStore.sendTyping(normalizedConversationId)"
        />
      </div>
    </div>

    <div v-else class="empty-state !py-20">
      <div class="empty-state__icon" aria-hidden="true">💬</div>
      <p class="empty-state__title">会话不存在</p>
      <p class="empty-state__copy">这个会话可能已被移除，或没有访问权限。</p>
    </div>
  </section>
</template>
