<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChatConversationList from '@/components/chat/ChatConversationList.vue'
import { useChatStore } from '@/stores/chat'
import { useNotificationsStore } from '@/stores/notifications'

const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()
const notifStore = useNotificationsStore()

const activeTab = ref(route.query.tab === 'notifications' ? 'notifications' : 'chat')
const notifPage = ref(1)
const showUnreadOnly = ref(false)

const conversations = computed(() => chatStore.conversations)
const chatLoading = computed(() => chatStore.loading)
const chatUnread = computed(() => Number(chatStore.unreadTotal || 0))

const notifications = computed(() => notifStore.notifications)
const notifTotal = computed(() => notifStore.total)
const notifUnread = computed(() => Number(notifStore.unreadCount || 0))
const notifLoading = computed(() => notifStore.loading)
const notifPageCount = computed(() => Math.ceil(notifTotal.value / 20) || 1)

const NOTIFICATION_TYPE_META = {
  ORDER_ACCEPTED: { icon: '📋', label: '接单通知' },
  ORDER_DELIVERED: { icon: '📦', label: '交付通知' },
  ORDER_CONFIRMED: { icon: '✅', label: '完成通知' },
  ORDER_DISPUTED: { icon: '⚠️', label: '争议通知' },
  ORDER_CANCELLED: { icon: '❌', label: '取消通知' },
  NEW_MESSAGE: { icon: '💬', label: '消息通知' },
  APPLICATION_APPROVED: { icon: '🎉', label: '申请通过' },
  APPLICATION_REJECTED: { icon: '😞', label: '申请拒绝' },
  REVIEW_RECEIVED: { icon: '⭐', label: '评价通知' },
  SYSTEM_ANNOUNCEMENT: { icon: '📢', label: '系统公告' },
}

function typeMeta(type) {
  return NOTIFICATION_TYPE_META[type] || { icon: '🔔', label: '通知' }
}

function formatTime(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const diffMs = now - d
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHours = Math.floor(diffMin / 60)
  if (diffHours < 24) return `${diffHours} 小时前`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 7) return `${diffDays} 天前`
  return d.toLocaleDateString('zh-CN')
}

function switchTab(tab) {
  activeTab.value = tab
  router.replace({ query: { ...route.query, tab } })
}

function openConversation(conversation) {
  router.push({ name: 'chat-detail', params: { id: conversation.id } })
}

async function loadNotifPage(p = 1) {
  notifPage.value = p
  await notifStore.fetchNotifications({ page: p, unreadOnly: showUnreadOnly.value })
}

async function toggleUnreadFilter() {
  showUnreadOnly.value = !showUnreadOnly.value
  await loadNotifPage(1)
}

async function handleNotifClick(n) {
  if (!n.is_read) {
    await notifStore.markRead(n.id)
  }
  if (n.link) {
    router.push(n.link)
  }
}

async function handleMarkAllRead() {
  await notifStore.markAllRead()
}

onMounted(async () => {
  await chatStore.fetchConversations()
  await chatStore.fetchUnreadSummary()
  await loadNotifPage(1)
})
</script>

<template>
  <div class="page-shell space-y-6">
    <!-- Header -->
    <section class="hero-panel scanline-overlay p-6 sm:p-8">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 class="section-title neon-text !text-4xl">消息中心</h1>
          <p class="mt-2 text-sm text-slate-400">
            {{ chatUnread + notifUnread }} 条未读
          </p>
        </div>

        <!-- Tab switcher -->
        <div class="flex gap-2">
          <button
            class="filter-pill flex items-center gap-2"
            :class="activeTab === 'chat' ? 'filter-pill-active' : ''"
            @click="switchTab('chat')"
          >
            <span>聊天</span>
            <span
              v-if="chatUnread > 0"
              class="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary-500 px-1.5 text-[10px] font-bold text-white"
            >{{ chatUnread }}</span>
          </button>
          <button
            class="filter-pill flex items-center gap-2"
            :class="activeTab === 'notifications' ? 'filter-pill-active' : ''"
            @click="switchTab('notifications')"
          >
            <span>通知</span>
            <span
              v-if="notifUnread > 0"
              class="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary-500 px-1.5 text-[10px] font-bold text-white"
            >{{ notifUnread }}</span>
          </button>
        </div>
      </div>
    </section>

    <!-- Chat Tab -->
    <template v-if="activeTab === 'chat'">
      <section class="surface-card cyber-corner p-5 sm:p-6 lg:p-8">
        <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm uppercase tracking-[0.22em] text-primary-200">列表</p>
            <h2 class="mt-2 text-2xl font-semibold text-white">对话</h2>
          </div>
          <router-link to="/orders" class="btn-ghost !px-4">返回订单</router-link>
        </div>

        <ChatConversationList
          :conversations="conversations"
          :loading="chatLoading"
          @select="openConversation"
        />
      </section>
    </template>

    <!-- Notifications Tab -->
    <template v-if="activeTab === 'notifications'">
      <section class="surface-card p-5 sm:p-6">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <p class="text-sm text-slate-400">
            共 {{ notifTotal }} 条通知，{{ notifUnread }} 条未读
          </p>
          <div class="flex gap-3">
            <button
              class="btn-ghost !px-4"
              :class="showUnreadOnly ? 'ring-1 ring-primary-400' : ''"
              @click="toggleUnreadFilter"
            >
              {{ showUnreadOnly ? '查看全部' : '只看未读' }}
            </button>
            <button
              v-if="notifUnread > 0"
              class="btn-secondary !px-4"
              @click="handleMarkAllRead"
            >
              全部已读
            </button>
          </div>
        </div>
      </section>

      <section v-if="notifLoading" class="space-y-3" aria-busy="true">
        <div v-for="n in 5" :key="`notif-skeleton-${n}`" class="info-tile flex items-start gap-4">
          <div class="skeleton h-8 w-8 shrink-0 !rounded-full"></div>
          <div class="flex-1 space-y-2.5">
            <div class="skeleton-line h-3.5 w-1/3"></div>
            <div class="skeleton-line h-3 w-2/3"></div>
          </div>
        </div>
      </section>

      <section v-else-if="notifications.length === 0" class="empty-state !py-14">
        <div class="empty-state__icon" aria-hidden="true">🔔</div>
        <h2 class="empty-state__title">暂无通知</h2>
        <p class="empty-state__copy">订单进展和系统公告都会推送到这里。</p>
      </section>

      <section v-else class="space-y-3">
        <button
          v-for="n in notifications"
          :key="n.id"
          type="button"
          class="surface-card flex w-full items-start gap-4 !rounded-tile p-4 text-left transition-all duration-200 hover:!border-line-strong hover:bg-white/[0.055] sm:p-5"
          :class="n.is_read ? 'opacity-60' : 'border-l-2 !border-l-primary-400 bg-primary-500/[0.05]'"
          @click="handleNotifClick(n)"
        >
          <span class="mt-0.5 text-2xl">{{ typeMeta(n.type).icon }}</span>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="text-xs text-primary-300">{{ typeMeta(n.type).label }}</span>
              <span v-if="!n.is_read" class="h-2 w-2 rounded-full bg-primary-400 shadow-glow-neon"></span>
            </div>
            <h3 class="mt-1 text-sm font-semibold text-white">{{ n.title }}</h3>
            <p class="mt-1 text-sm text-slate-400">{{ n.content }}</p>
            <p class="mt-2 text-xs text-slate-500">{{ formatTime(n.created_at) }}</p>
          </div>
        </button>
      </section>

      <div v-if="notifPageCount > 1" class="flex items-center justify-center gap-2 py-4">
        <button
          v-for="p in notifPageCount"
          :key="p"
          class="filter-pill"
          :class="p === notifPage ? 'filter-pill-active' : ''"
          @click="loadNotifPage(p)"
        >
          {{ p }}
        </button>
      </div>
    </template>
  </div>
</template>
