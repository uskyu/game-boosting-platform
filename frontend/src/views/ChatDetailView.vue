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
        <p class="text-xs uppercase tracking-[0.12em] text-primary">会话</p>
        <p class="mt-2 text-sm text-ink-2">
          {{ activeConversation?.order ? `订单 #${activeConversation.order.id}` : `会话 #${normalizedConversationId}` }}
        </p>
      </div>
    </div>

    <!-- 双栏固定高度（桌面双栏 / 移动端单聊天窗，均可视口内滚动）：
         会话列表在框内滚动，不再有多少会话就把页面拉多长、把聊天窗顶到页面底部；
         ChatPanel 自带 h-full+内部滚动，填满即可 -->
    <section class="grid h-[calc(100vh-240px)] min-h-[420px] gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
      <aside class="surface-card cyber-corner hidden flex-col overflow-hidden p-4 sm:p-5 xl:flex">
        <div class="mb-4 flex items-center justify-between gap-3 px-2">
          <div>
            <p class="text-xs uppercase tracking-[0.12em] text-primary">列表</p>
            <h2 class="mt-2 text-xl font-semibold text-ink-1">消息列表</h2>
          </div>
          <router-link to="/orders" class="btn-ghost !px-4">
            订单
          </router-link>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto pr-1">
          <ChatConversationList
            :conversations="conversations"
            :active-conversation-id="normalizedConversationId"
            :loading="chatStore.loading"
            @select="openConversation"
          />
        </div>
      </aside>

      <ChatPanel :conversation-id="normalizedConversationId" />
    </section>
  </div>
</template>
