<script setup>
import { computed } from 'vue'

const props = defineProps({
  count: {
    type: Number,
    default: 0,
  },
  dotOnly: {
    type: Boolean,
    default: false,
  },
})

const normalizedCount = computed(() => Number(props.count || 0))
const visible = computed(() => normalizedCount.value > 0)
const displayCount = computed(() => (
  normalizedCount.value > 99 ? '99+' : normalizedCount.value
))
</script>

<template>
  <span
    v-if="visible"
    :class="['chat-unread-badge', { 'chat-unread-badge-dot': dotOnly }]"
  >
    <span v-if="!dotOnly">{{ displayCount }}</span>
  </span>
</template>
