<script setup>
defineProps({
  name: {
    type: String,
    required: true,
  },
})
</script>

<template>
  <!-- 大厅 -->
  <svg v-if="name === 'hall'" class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="4" y="3.5" width="16" height="17" rx="3" />
    <path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h4.5" />
  </svg>
  <!-- 订单 -->
  <svg v-else-if="name === 'orders'" class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M7 3.5h10a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z" />
    <path d="M9.5 8.5h5M9.5 12h5M9.5 15.5h3" />
  </svg>
  <!-- 钱包 -->
  <svg v-else-if="name === 'wallet'" class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="3" y="6" width="18" height="13" rx="3" />
    <path d="M3 10h13" />
    <circle cx="17.2" cy="14.5" r="1" fill="currentColor" stroke="none" />
  </svg>
  <!-- 消息 -->
  <svg v-else-if="name === 'messages'" class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  </svg>
  <!-- 我的 -->
  <svg v-else-if="name === 'profile'" class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="8" r="4" />
    <path d="M4 20.5c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5" />
  </svg>
  <!-- 运营（管理员） -->
  <svg v-else-if="name === 'admin'" class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 2.5 3 7v6c0 5 3.8 7.7 9 9 5.2-1.3 9-4 9-9V7l-9-4.5z" />
    <path d="M9 12l2 2 4-4.5" />
  </svg>
</template>
