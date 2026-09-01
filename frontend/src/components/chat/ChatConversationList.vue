<script setup>
import { computed } from 'vue'
import { formatShortDate } from '@/utils/display'
import { useChatStore } from '@/stores/chat'
import ChatUnreadBadge from './ChatUnreadBadge.vue'

const chatStore = useChatStore()

const props = defineProps({
  conversations: {
    type: Array,
    default: () => [],
  },
  activeConversationId: {
    type: [Number, String],
    default: null,
  },
  loading: {
    type: Boolean,
    default: false,
  },
  emptyTitle: {
    type: String,
    default: '暂无对话',
  },
  emptyDescription: {
    type: String,
    default: '新会话会出现在这里。',
  },
})

const emit = defineEmits(['select', 'toggle-pin'])

const sortedItems = computed(() => [...(props.conversations || [])].sort((left, right) => {
  const pinDifference = Number(Boolean(right.is_pinned)) - Number(Boolean(left.is_pinned))
  if (pinDifference !== 0) return pinDifference
  return new Date(right.last_message_at || right.updated_at || right.created_at || 0).getTime()
    - new Date(left.last_message_at || left.updated_at || left.created_at || 0).getTime()
}))

function getDisplayUsers(conversation) {
  const names = (conversation.other_participants || [])
    .map((item) => item.username)
    .filter(Boolean)

  if (names.length) {
    return names
  }

  return (conversation.participants || [])
    .map((item) => item.user?.username)
    .filter(Boolean)
}

function getTitle(conversation) {
  const names = getDisplayUsers(conversation)
  if (names.length) {
    return names.join(' / ')
  }

  if (conversation.order?.id) {
    return `订单 #${conversation.order.id}`
  }

  return `会话 #${conversation.id}`
}

function getSubtitle(conversation) {
  if (conversation.order) {
    return `${conversation.order.game_name} · ${conversation.order.current_rank} → ${conversation.order.target_rank}`
  }

  return '站内会话'
}

function getPreview(conversation) {
  return conversation.last_message_preview || '还没有消息'
}

function getAvatarLabel(conversation) {
  const title = getTitle(conversation)
  return title.slice(0, 1).toUpperCase()
}

function getTimestamp(conversation) {
  return formatShortDate(
    conversation.last_message_at
      || conversation.updated_at
      || conversation.created_at
  )
}

function hasAdmin(conversation) {
  return (conversation.participants || [])
    .some((participant) => participant.user?.role === 'ADMIN' || participant.role_snapshot === 'ADMIN')
}

function handleSelect(conversation) {
  emit('select', conversation)
}

async function handleTogglePin(event, conversation) {
  event.stopPropagation()
  await chatStore.toggleConversationPinned(conversation)
}
</script>

<template>
  <div class="chat-conversation-list">
    <div
      v-if="loading && !sortedItems.length"
      class="space-y-3"
      aria-busy="true"
    >
      <div v-for="n in 3" :key="`conv-skeleton-${n}`" class="chat-conversation-card !cursor-default">
        <div class="flex items-start gap-4">
          <div class="skeleton h-12 w-12 !rounded-tile"></div>
          <div class="min-w-0 flex-1 space-y-3 pt-1">
            <div class="skeleton-line h-3.5 w-1/2"></div>
            <div class="skeleton-line h-3 w-2/3"></div>
          </div>
        </div>
      </div>
    </div>

    <div
      v-else-if="!sortedItems.length"
      class="empty-state !py-10"
    >
      <div class="empty-state__icon" aria-hidden="true">💬</div>
      <p class="empty-state__title">{{ emptyTitle }}</p>
      <p class="empty-state__copy">{{ emptyDescription }}</p>
    </div>

    <div v-else class="space-y-3">
      <button
        v-for="conversation in sortedItems"
        :key="conversation.id"
        type="button"
        class="chat-conversation-card"
        :class="{
          'chat-conversation-card-active': Number(activeConversationId) === Number(conversation.id),
        }"
        @click="handleSelect(conversation)"
      >
        <div class="flex items-start gap-4">
          <div class="chat-avatar">
            {{ getAvatarLabel(conversation) }}
          </div>

          <div class="min-w-0 flex-1 text-left">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <p class="truncate text-sm font-semibold text-ink-1">
                    {{ getTitle(conversation) }}
                  </p>
                  <span
                    v-if="hasAdmin(conversation)"
                    class="rounded-full bg-warning-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-warning"
                  >
                    管理员
                  </span>
                  <span
                    v-if="conversation.is_pinned"
                    class="text-warning"
                    title="已置顶"
                    aria-label="已置顶"
                  >
                    <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                      <path stroke-linecap="round" stroke-linejoin="round" d="m14 4 6 6-3 1-4 5v4l-2-2-2 2v-4l-5-5-1-3 6 1 5-5z" />
                    </svg>
                  </span>
                </div>
                <p class="mt-1 truncate text-xs text-ink-3">
                  {{ getSubtitle(conversation) }}
                </p>
              </div>

              <div class="flex flex-col items-end gap-2">
                <span class="text-[11px] text-ink-3">{{ getTimestamp(conversation) }}</span>
                <button
                  type="button"
                  class="min-h-11 min-w-11 rounded-full p-2 text-ink-3 transition hover:bg-surface-3 hover:text-warning"
                  :title="conversation.is_pinned ? '取消置顶' : '置顶会话'"
                  :aria-label="conversation.is_pinned ? '取消置顶' : '置顶会话'"
                  @click="handleTogglePin($event, conversation)"
                >
                  <svg viewBox="0 0 24 24" class="mx-auto h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" d="m14 4 6 6-3 1-4 5v4l-2-2-2 2v-4l-5-5-1-3 6 1 5-5z" />
                  </svg>
                </button>
                <ChatUnreadBadge :count="Number(conversation.unread_count || 0)" />
              </div>
            </div>

            <p class="mt-4 truncate text-sm text-ink-2">
              {{ getPreview(conversation) }}
            </p>
          </div>
        </div>
      </button>
    </div>
  </div>
</template>
