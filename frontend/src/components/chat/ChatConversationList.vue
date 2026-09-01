<script setup>
import { computed } from 'vue'
import { formatShortDate } from '@/utils/display'
import ChatUnreadBadge from './ChatUnreadBadge.vue'

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

const emit = defineEmits(['select'])

const sortedItems = computed(() => props.conversations || [])

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
                    客服介入
                  </span>
                </div>
                <p class="mt-1 truncate text-xs text-ink-3">
                  {{ getSubtitle(conversation) }}
                </p>
              </div>

              <div class="flex flex-col items-end gap-2">
                <span class="text-[11px] text-ink-3">{{ getTimestamp(conversation) }}</span>
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
