<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChatUnreadBadge from '@/components/chat/ChatUnreadBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useNotificationsStore } from '@/stores/notifications'
import { getUserRoleMeta } from '@/utils/order'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const notificationsStore = useNotificationsStore()

let unreadPollingTimer = null

const copy = {
  brandTitle: '\u6e38\u620f\u670d\u52a1\u5e73\u53f0',
  home: '\u9996\u9875',
  enterZone: '\u6e38\u620f\u8be6\u60c5',
  orderHall: '\u8ba2\u5355\u5927\u5385',
  myOrders: '\u6211\u7684\u8ba2\u5355',
  boosterDesk: '\u966a\u73a9\u5de5\u4f5c\u53f0',
  serviceMarket: '\u966a\u73a9\u5e02\u573a',
  messages: '\u6d88\u606f',
  ops: '\u8fd0\u8425',
  searchOrders: '\u641c\u7d22\u9700\u6c42 / \u6807\u7b7e',
  profile: '\u4e2a\u4eba\u4e2d\u5fc3',
  logout: '\u9000\u51fa\u767b\u5f55',
  login: '\u767b\u5f55',
  signup: '\u7acb\u5373\u6ce8\u518c',
  footerLine: '\u9996\u9875\u5148\u8fdb\u573a\uff0c\u518d\u9009\u4e13\u533a\u548c\u7cbe\u786e\u641c\u7d22',
  advancedSearch: '\u9ad8\u7ea7\u641c\u7d22',
}

const isAuthenticated = computed(() => authStore.isAuthenticated)
const isBooster = computed(() => authStore.isBooster)
const isAdmin = computed(() => authStore.isAdmin)
const user = computed(() => authStore.user)
const roleMeta = computed(() => getUserRoleMeta(user.value?.role))
const unreadTotal = computed(() => Number(chatStore.unreadTotal || 0))
const notifUnread = computed(() => Number(notificationsStore.unreadCount || 0))
const hideFooter = computed(() => ['login', 'register', 'message-center', 'chat-detail', 'settings', 'support'].includes(route.name))
const isHomeRoute = computed(() => route.name === 'home')

const primaryNavItems = computed(() => [
  {
    key: 'home',
    label: copy.home,
    show: true,
    active: route.name === 'home' && !route.hash,
    action: () => router.push({ name: 'home' }),
    badge: 0,
  },
  {
    key: 'enter-zone',
    label: copy.enterZone,
    show: true,
    active: route.name === 'home',
    action: () => openHomeAnchor('#match-floor'),
    badge: 0,
  },
  {
    key: 'orders',
    label: isBooster.value ? copy.orderHall : copy.myOrders,
    show: isAuthenticated.value,
    active: ['orders', 'order-detail', 'order-create'].includes(route.name),
    action: () => router.push({ name: 'orders' }),
    badge: 0,
  },
  {
    key: 'services',
    label: isBooster.value ? copy.boosterDesk : copy.serviceMarket,
    show: isAuthenticated.value && !isAdmin.value,
    active: ['services', 'service-detail'].includes(route.name),
    action: () => router.push({ name: 'services' }),
    badge: 0,
  },
  {
    key: 'wallet',
    label: '\u94b1\u5305',
    show: isAuthenticated.value,
    active: route.name === 'wallet',
    action: () => router.push({ name: 'wallet' }),
    badge: 0,
  },
  {
    key: 'messages',
    label: copy.messages,
    show: isAuthenticated.value,
    active: ['message-center', 'chat-detail'].includes(route.name),
    action: () => router.push({ name: 'message-center' }),
    badge: unreadTotal.value + notifUnread.value,
  },
  {
    key: 'admin',
    label: copy.ops,
    show: isAuthenticated.value && isAdmin.value,
    active: route.name === 'admin',
    action: () => router.push({ name: 'admin' }),
    badge: 0,
  },
].filter((item) => item.show))

function openHomeAnchor(hash) {
  if (route.name === 'home') {
    const target = document.querySelector(hash)
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }

  router.push({ name: 'home', hash })
}

function stopUnreadPolling() {
  if (unreadPollingTimer) {
    window.clearInterval(unreadPollingTimer)
    unreadPollingTimer = null
  }
}

function openSearch() {
  router.push({ name: 'search', query: { type: 'all' } })
}

function startUnreadPolling() {
  stopUnreadPolling()

  unreadPollingTimer = window.setInterval(() => {
    if (authStore.isAuthenticated && chatStore.socketStatus !== 'connected') {
      chatStore.fetchUnreadSummary()
    }
  }, 45000)
}

async function syncChatLifecycle(isLoggedIn) {
  if (isLoggedIn) {
    await chatStore.fetchUnreadSummary()
    await notificationsStore.fetchUnreadCount()
    chatStore.connectWebSocket()
    startUnreadPolling()
    return
  }

  stopUnreadPolling()
  chatStore.disconnectWebSocket({ clearState: true })
  notificationsStore.resetState()
}

async function handleLogout() {
  stopUnreadPolling()
  chatStore.disconnectWebSocket({ clearState: true })
  authStore.logout()
  router.push({ name: 'login' })
}

watch(
  isAuthenticated,
  async (isLoggedIn) => {
    await syncChatLifecycle(isLoggedIn)
  },
  { immediate: true }
)

watch(
  () => chatStore.socketStatus,
  async (status) => {
    if (isAuthenticated.value && status === 'connected') {
      await chatStore.fetchUnreadSummary()
    }
  }
)

onBeforeUnmount(() => {
  stopUnreadPolling()
  chatStore.disconnectWebSocket()
})
</script>

<template>
  <div class="min-h-screen">
    <nav :class="['app-header', { 'app-header-floating': isHomeRoute }]">
      <div class="shell-container flex min-h-20 flex-wrap items-center justify-between gap-4 py-4">
        <button type="button" class="brand-lockup text-left" @click="router.push({ name: 'home' })">
          <span class="brand-mark !h-9 !w-9 !text-sm" aria-hidden="true">◆</span>
          <p class="brand-lockup__title text-base text-white">{{ copy.brandTitle }}</p>
        </button>

        <div class="hidden flex-1 items-center justify-center gap-2 xl:flex">
          <button
            v-for="item in primaryNavItems"
            :key="item.key"
            type="button"
            :class="item.active ? 'app-nav-link app-nav-link-active' : 'app-nav-link'"
            @click="item.action"
          >
            <span>{{ item.label }}</span>
            <ChatUnreadBadge v-if="item.badge" :count="item.badge" />
          </button>
        </div>

        <div class="flex flex-wrap items-center justify-end gap-2 sm:gap-3">
          <button type="button" class="app-command hidden lg:inline-flex" @click="openSearch">
            {{ copy.searchOrders }}
          </button>

          <span v-if="isAuthenticated" :class="roleMeta.badgeClass">
            {{ roleMeta.label }}
          </span>

          <template v-if="isAuthenticated">
            <router-link to="/profile" class="btn-secondary !px-4">
              {{ user?.username || copy.profile }}
            </router-link>
            <router-link to="/settings" class="btn-ghost !px-3" title="设置">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clip-rule="evenodd" />
              </svg>
            </router-link>
            <button class="btn-ghost !px-4" @click="handleLogout">{{ copy.logout }}</button>
          </template>
          <template v-else>
            <router-link to="/login" class="btn-ghost !px-4">{{ copy.login }}</router-link>
            <router-link to="/register" class="btn-primary !px-4">{{ copy.signup }}</router-link>
          </template>
        </div>
      </div>

      <div class="shell-container flex flex-col gap-3 pb-4 xl:hidden">
        <div class="flex gap-2 overflow-x-auto">
          <button
            v-for="item in primaryNavItems"
            :key="`${item.key}-mobile`"
            type="button"
            :class="item.active ? 'app-nav-link app-nav-link-active whitespace-nowrap' : 'app-nav-link whitespace-nowrap'"
            @click="item.action"
          >
            <span>{{ item.label }}</span>
            <ChatUnreadBadge v-if="item.badge" :count="item.badge" />
          </button>
        </div>

        <button type="button" class="app-command w-full justify-center" @click="openSearch">
          {{ copy.searchOrders }}
        </button>
      </div>
    </nav>

    <main class="relative z-10">
      <router-view v-slot="{ Component }">
        <transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <!-- Floating AI support button -->
    <router-link
      v-if="isAuthenticated && route.name !== 'support'"
      to="/support"
      class="group fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full border border-primary-300/40 bg-gradient-to-br from-primary-500/95 to-primary-600 text-2xl shadow-glow transition-all duration-200 hover:scale-110 hover:shadow-glow-pink"
      title="AI 客服"
    >
      <span class="drop-shadow-[0_2px_6px_rgba(0,0,0,0.45)] transition-transform duration-200 group-hover:-translate-y-0.5">🤖</span>
    </router-link>

    <footer v-if="!hideFooter" class="mt-4 border-t border-line-soft bg-surface-0/70 backdrop-blur-xl">
      <div class="shell-container flex flex-col gap-8 py-10 lg:flex-row lg:items-start lg:justify-between">
        <div class="max-w-sm space-y-3">
          <div class="brand-lockup">
            <span class="brand-mark !h-8 !w-8 !text-xs" aria-hidden="true">◆</span>
            <p class="brand-lockup__title text-sm text-white">{{ copy.brandTitle }}</p>
          </div>
          <p class="text-sm leading-7 text-slate-500">{{ copy.footerLine }}</p>
        </div>

        <div class="grid gap-8 sm:grid-cols-2">
          <div class="space-y-3">
            <p class="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">导航</p>
            <div class="flex flex-col items-start gap-2.5">
              <router-link to="/search?type=all" class="text-sm text-slate-400 transition-colors duration-200 hover:text-primary-100">{{ copy.advancedSearch }}</router-link>
              <router-link v-if="isAuthenticated" to="/orders" class="text-sm text-slate-400 transition-colors duration-200 hover:text-primary-100">{{ copy.orderHall }}</router-link>
              <router-link v-if="isAuthenticated" to="/wallet" class="text-sm text-slate-400 transition-colors duration-200 hover:text-primary-100">钱包</router-link>
            </div>
          </div>
          <div class="space-y-3">
            <p class="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">服务</p>
            <div class="flex flex-col items-start gap-2.5">
              <button type="button" class="text-sm text-slate-400 transition-colors duration-200 hover:text-primary-100" @click="openHomeAnchor('#match-floor')">{{ copy.enterZone }}</button>
              <router-link v-if="isAuthenticated && !isAdmin" to="/services" class="text-sm text-slate-400 transition-colors duration-200 hover:text-primary-100">{{ isBooster ? copy.boosterDesk : copy.serviceMarket }}</router-link>
              <router-link v-if="isAuthenticated" to="/support" class="text-sm text-slate-400 transition-colors duration-200 hover:text-primary-100">AI客服</router-link>
            </div>
          </div>
        </div>
      </div>
    </footer>
  </div>
</template>
