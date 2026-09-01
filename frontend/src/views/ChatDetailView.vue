<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '@/stores/chat'
import ChatConversationList from '@/components/chat/ChatConversationList.vue'
import ChatPanel from '@/components/chat/ChatPanel.vue'

const props = defineProps({
  id: {
    type: [String, Number],
    required: true,
  },
})

const router = useRouter()
const chatStore = useChatStore()

const normalizedConversationId = computed(() => Number(props.id))
const conversations = computed(() => chatStore.conversations)
const activeConversation = computed(() => (
  chatStore.conversations.find((item) => item.id === normalizedConversationId.value) || null
))

function openConversation(conversation) {
  if (Number(conversation.id) === normalizedConversationId.value) {
    return
  }

  router.push({
    name: 'chat-detail',
    params: { id: conversation.id },
  })
}

watch(
  () => normalizedConversationId.value,
  async (conversationId) => {
    if (!conversationId) {
      return
    }

    if (!chatStore.conversations.some((item) => item.id === conversationId)) {
      await chatStore.fetchConversation(conversationId)
    }
  },
  { immediate: true }
)

onMounted(async () => {
  if (!chatStore.conversations.length) {
    await chatStore.fetchConversations()
  }
  await chatStore.fetchUnreadSummary()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <button class="btn-ghost !px-0 text-sm" @click="router.push({ name: 'message-center', query: { tab: 'chat' } })">
        返回消息列表
      </button>

      <div class="text-right">
        <p class="text-xs uppercase tracking-[0.24em] text-primary-200">会话</p>
        <p class="mt-2 text-sm text-slate-400">
          {{ activeConversation?.order ? `订单 #${activeConversation.order.id}` : `会话 #${normalizedConversationId}` }}
        </p>
      </div>
    </div>

    <section class="grid min-h-[78vh] gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
      <aside class="surface-card cyber-corner p-4 sm:p-5">
        <div class="mb-4 flex items-center justify-between gap-3 px-2">
          <div>
            <p class="text-xs uppercase tracking-[0.22em] text-primary-200">列表</p>
            <h2 class="mt-2 text-xl font-semibold text-white">消息列表</h2>
          </div>
          <router-link to="/orders" class="btn-ghost !px-4">
            订单
          </router-link>
        </div>

        <ChatConversationList
          :conversations="conversations"
          :active-conversation-id="normalizedConversationId"
          :loading="chatStore.loading"
          @select="openConversation"
        />
      </aside>

      <ChatPanel :conversation-id="normalizedConversationId" />
    </section>
  </div>
</template>
