<script setup>
import { computed } from 'vue'

const props = defineProps({
  conversation: {
    type: Object,
    default: null,
  },
  typingUserId: {
    type: [Number, String],
    default: null,
  },
})

const displayName = computed(() => {
  if (!props.typingUserId || !props.conversation) {
    return ''
  }

  const normalizedUserId = Number(props.typingUserId)
  const participant = props.conversation.other_participants?.find(
    (item) => Number(item.id) === normalizedUserId
  )
    || props.conversation.participants?.find(
      (item) => Number(item.user_id) === normalizedUserId
    )?.user

  return participant?.username || '对方'
})
</script>

<template>
  <div v-if="typingUserId" class="chat-typing-indicator">
    <span class="chat-typing-dot"></span>
    <span class="chat-typing-dot"></span>
    <span class="chat-typing-dot"></span>
    <span>{{ displayName }} 正在输入...</span>
  </div>
</template>
