<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chat'
import ChatConversationList from '@/components/chat/ChatConversationList.vue'

const router = useRouter()
const chatStore = useChatStore()

const conversations = computed(() => chatStore.conversations)
const loading = computed(() => chatStore.loading)

function openConversation(conversation) {
  router.push({
    name: 'chat-detail',
    params: { id: conversation.id },
  })
}

onMounted(async () => {
  await chatStore.fetchConversations()
  await chatStore.fetchUnreadSummary()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel scanline-overlay p-6 sm:p-8 lg:p-10">
      <div class="max-w-4xl space-y-4">
        <span class="eyebrow">消息</span>
        <h1 class="section-title neon-text !text-4xl sm:!text-5xl">
          消息
        </h1>
        <p class="section-copy max-w-3xl">
          订单里的聊天都在这。
        </p>
      </div>
    </section>

    <section class="surface-card cyber-corner p-5 sm:p-6 lg:p-8">
      <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="text-sm uppercase tracking-[0.22em] text-primary-200">列表</p>
          <h2 class="mt-2 text-2xl font-semibold text-white">对话</h2>
        </div>
        <router-link to="/orders" class="btn-ghost !px-4">
          返回订单
        </router-link>
      </div>

      <ChatConversationList
        :conversations="conversations"
        :loading="loading"
        @select="openConversation"
      />
    </section>
  </div>
</template>
