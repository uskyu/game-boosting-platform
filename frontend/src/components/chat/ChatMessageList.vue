<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import ChatMessageBubble from './ChatMessageBubble.vue'
import ChatTypingIndicator from './ChatTypingIndicator.vue'

const props = defineProps({
  messages: {
    type: Array,
    default: () => [],
  },
  conversation: {
    type: Object,
    default: null,
  },
  currentUserId: {
    type: [Number, String],
    default: null,
  },
  hasMore: {
    type: Boolean,
    default: false,
  },
  loadingMore: {
    type: Boolean,
    default: false,
  },
  typingUserId: {
    type: [Number, String],
    default: null,
  },
  readReceiptMessageId: {
    type: [Number, String],
    default: null,
  },
  emptyText: {
    type: String,
    default: '还没有消息',
  },
  highlightedMessageId: {
    type: [Number, String],
    default: null,
  },
})

const emit = defineEmits(['load-more', 'recall'])

const containerRef = ref(null)
const shouldRestoreScroll = ref(false)
const previousScrollHeight = ref(0)

function isNearBottom() {
  const element = containerRef.value
  if (!element) {
    return true
  }

  return element.scrollHeight - element.scrollTop - element.clientHeight < 120
}

function scrollToBottom(force = false) {
  const element = containerRef.value
  if (!element) {
    return
  }

  if (!force && !isNearBottom()) {
    return
  }

  element.scrollTop = element.scrollHeight
}

function scrollToMessage(messageId) {
  const element = containerRef.value
  if (!element || !messageId) {
    return
  }

  const target = element.querySelector(`[data-message-id="${messageId}"]`)
  if (!target) {
    return
  }

  target.scrollIntoView({
    behavior: 'smooth',
    block: 'center',
  })
}

function handleScroll() {
  const element = containerRef.value
  if (!element || props.loadingMore || !props.hasMore) {
    return
  }

  if (element.scrollTop <= 32) {
    shouldRestoreScroll.value = true
    previousScrollHeight.value = element.scrollHeight
    emit('load-more')
  }
}

watch(
  () => props.messages.map((message) => message.id).join(','),
  async (nextValue, previousValue) => {
    if (!nextValue) {
      return
    }

    const stickToBottom = isNearBottom()
    await nextTick()

    if (!previousValue) {
      scrollToBottom(true)
      return
    }

    if (stickToBottom) {
      scrollToBottom(true)
    }
  }
)

watch(
  () => props.loadingMore,
  async (isLoading, wasLoading) => {
    if (wasLoading && !isLoading && shouldRestoreScroll.value && containerRef.value) {
      await nextTick()
      containerRef.value.scrollTop = containerRef.value.scrollHeight - previousScrollHeight.value
      shouldRestoreScroll.value = false
    }
  }
)

watch(
  () => props.highlightedMessageId,
  async (messageId) => {
    if (!messageId) {
      return
    }

    await nextTick()
    scrollToMessage(messageId)
  }
)

onMounted(async () => {
  await nextTick()
  scrollToBottom(true)
})

defineExpose({
  scrollToBottom,
  scrollToMessage,
})
</script>

<template>
  <div
    ref="containerRef"
    class="chat-message-list"
    @scroll="handleScroll"
  >
    <div
      v-if="hasMore"
      class="pb-4 text-center text-xs tracking-[0.1em] text-ink-3"
    >
      {{ loadingMore ? '加载中...' : '上滑看更早消息' }}
    </div>

    <div v-if="!messages.length" class="empty-state !py-12">
      {{ emptyText }}
    </div>

    <div v-else class="space-y-4">
      <div
        v-for="message in messages"
        :key="message.id"
        :data-message-id="message.id"
        class="transition duration-300"
        :class="{
          'rounded-card ring-1 ring-primary': Number(highlightedMessageId) === Number(message.id),
        }"
      >
        <ChatMessageBubble
          :message="message"
          :current-user-id="currentUserId"
          :show-read-receipt="Number(readReceiptMessageId) === Number(message.id)"
          :can-recall="
            Number(message.sender_id) === Number(currentUserId)
              && !message.recalled_at
              && message.message_type !== 'SYSTEM'
              && Date.now() - new Date(message.created_at).getTime() <= 2 * 60 * 1000
          "
          @recall="emit('recall', $event)"
        />
      </div>
    </div>

    <ChatTypingIndicator
      v-if="typingUserId"
      class="mt-4"
      :conversation="conversation"
      :typing-user-id="typingUserId"
    />
  </div>
</template>
