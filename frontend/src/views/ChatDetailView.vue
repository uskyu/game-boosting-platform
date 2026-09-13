<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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

// 按设备真实视口适配：实测顶部导航、底部固定标签栏、页壳自身留白，
// 剩余高度全部给聊天双栏（消息可视区最大化），窗口变化实时跟随。
const rootRef = ref(null)
const shellHeight = ref('78vh')

function updateShellHeight() {
  const rootEl = rootRef.value
  if (!rootEl) return
  let used = 0
  const header = document.querySelector('.app-header')
  if (header) used += header.getBoundingClientRect().height
  const tabbar = document.querySelector('.app-tabbar')
  if (tabbar && getComputedStyle(tabbar).display !== 'none') {
    used += tabbar.getBoundingClientRect().height
  }
  const cs = getComputedStyle(rootEl)
  used += parseFloat(cs.paddingTop || 0) + parseFloat(cs.paddingBottom || 0)
  const available = Math.max(window.innerHeight - used, 420)
  shellHeight.value = `${Math.round(available)}px`
}

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
  updateShellHeight()
  window.addEventListener('resize', updateShellHeight)
  // 字体/首屏渲染完成后 nav 高度可能微变，补测一次
  window.setTimeout(updateShellHeight, 400)

  if (!chatStore.conversations.length) {
    await chatStore.fetchConversations()
  }
  await chatStore.fetchUnreadSummary()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateShellHeight)
})
</script>

<template>
  <div ref="rootRef" class="page-shell flex flex-col gap-4 overflow-hidden !py-4 sm:!py-5" :style="{ height: shellHeight }">
    <div class="flex flex-wrap items-center justify-between gap-3 xl:hidden">
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
    <section class="grid min-h-0 flex-1 gap-4 sm:gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
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
