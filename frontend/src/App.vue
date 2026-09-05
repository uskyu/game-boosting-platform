<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppNavIcon from '@/components/AppNavIcon.vue'
import ChatUnreadBadge from '@/components/chat/ChatUnreadBadge.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useNotificationsStore } from '@/stores/notifications'
import { useSettingsStore } from '@/stores/settings'
import { useSiteStore } from '@/stores/site'
import { useToastsStore } from '@/stores/toasts'
import api from '@/utils/api'
import { getUserRoleMeta } from '@/utils/order'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const notificationsStore = useNotificationsStore()
const settingsStore = useSettingsStore()
const siteStore = useSiteStore()
const toastsStore = useToastsStore()

// 点击网页内推送 toast：跳转目标页（如聊天会话）并消失
function handleToastClick(toast) {
  toastsStore.dismissToast(toast.id)
  if (toast.to) {
    router.push(toast.to)
  }
}

// 主题初始化：同步 localStorage / 系统偏好到 html.dark（index.html 已先行防闪烁）
settingsStore.initTheme()

let unreadPollingTimer = null

const NOTIF_POLL_INTERVAL = 10_000
let notifPollTimer = null
let knownNotifIds = null

async function pollOrderNotifications({ baseline = false } = {}) {
  if (!authStore.isAuthenticated || document.visibilityState !== 'visible') {
    return
  }
  let items = []
  try {
    const response = await api.get('/notifications', { params: { page: 1, page_size: 10 } })
    items = response.data?.items || []
  } catch {
    return
  }
  if (knownNotifIds === null) {
    knownNotifIds = new Set(items.map((n) => Number(n.id)))
    return
  }
  const newItems = items.filter((n) => !knownNotifIds.has(Number(n.id)))
  newItems.forEach((n) => knownNotifIds.add(Number(n.id)))
  // 只保留最近 200 个 ID，避免 Set 无限增长
  if (knownNotifIds.size > 200) {
    const ids = [...knownNotifIds].slice(-200)
    knownNotifIds = new Set(ids)
  }
  if (baseline || newItems.length === 0) {
    return
  }
  newItems.forEach((notification) => notificationsStore.announceOrderNotification(notification))
  try {
    await notificationsStore.fetchUnreadCount()
  } catch {
    // 角标刷新失败不影响列表
  }
}

function stopNotifPolling() {
  if (notifPollTimer) {
    window.clearInterval(notifPollTimer)
    notifPollTimer = null
  }
}

async function startNotifPolling() {
  stopNotifPolling()
  await pollOrderNotifications({ baseline: true })
  notifPollTimer = window.setInterval(() => {
    pollOrderNotifications().catch(() => {})
  }, NOTIF_POLL_INTERVAL)
}

const copy = {
  brandTitle: '游戏服务平台',
  hall: '大厅',
  games: '游戏',
  wallet: '钱包',
  messages: '消息',
  ops: '运营',
  mine: '我的',
  myOrders: '我的订单',
  serviceMarket: '陪玩市场',
  boosterDesk: '接单工作台',
  searchOrders: '搜索需求 / 标签',
  profile: '个人中心',
  logout: '退出登录',
  login: '登录',
  signup: '立即注册',
  footerLine: '大厅看单接单，专区挑游戏，钱包管收支。',
  advancedSearch: '高级搜索',
}

const isAuthenticated = computed(() => authStore.isAuthenticated)
const isAdmin = computed(() => authStore.isAdmin)
const user = computed(() => authStore.user)
const roleMeta = computed(() => getUserRoleMeta(isAdmin.value ? 'ADMIN' : 'BOOSTER'))
const unreadTotal = computed(() => Number(chatStore.unreadTotal || 0))
const notifUnread = computed(() => Number(notificationsStore.unreadCount || 0))
const messageBadge = computed(() => unreadTotal.value + notifUnread.value)
const avatarText = computed(() => user.value?.username?.slice(0, 1)?.toUpperCase() || '我')
const siteName = computed(() => siteStore.settings.site_name || '游戏服务平台')
const siteLogo = computed(() => siteStore.settings.site_logo_url || '')
watch([siteName, () => siteStore.settings.favicon_url], ([name, favicon]) => {
  document.title = name
  const link = document.querySelector('link[rel="icon"]')
  if (link && favicon) link.href = favicon
}, { immediate: true })
siteStore.fetchSettings()

// 沉浸式页面（登录/注册/聊天详情/联系管理员）不显示底部 Tab，其余页面固定 5 Tab
const immersiveRoutes = ['login', 'register', 'chat-detail', 'support']
const showTabBar = computed(() => !immersiveRoutes.includes(route.name))

// 桌面顶栏主导航（IA v2）：大厅 · 游戏 · 钱包 · 消息 · 运营(仅管理员)
// 桌面顶部导航：与移动端底部 Tab 同款五项（大厅 · 订单 · 钱包 · 消息 · 我的），管理员追加运营
const primaryNavItems = computed(() => [
  {
    key: 'hall',
    label: copy.hall,
    icon: 'hall',
    show: true,
    active: ['home', 'order-detail'].includes(route.name),
    action: () => router.push({ name: 'home' }),
    badge: 0,
  },
  {
    key: 'orders',
    label: copy.myOrders,
    icon: 'orders',
    show: true,
    active: ['orders', 'order-create'].includes(route.name),
    action: () => router.push({ name: 'orders' }),
    badge: 0,
  },
  {
    key: 'wallet',
    label: copy.wallet,
    icon: 'wallet',
    show: isAuthenticated.value,
    active: route.name === 'wallet',
    action: () => router.push({ name: 'wallet' }),
    badge: 0,
  },
  {
    key: 'messages',
    label: copy.messages,
    icon: 'messages',
    show: isAuthenticated.value,
    active: ['message-center', 'chat-detail'].includes(route.name),
    action: () => router.push({ name: 'message-center' }),
    badge: messageBadge.value,
  },
  {
    key: 'profile',
    label: '我的',
    icon: 'profile',
    show: isAuthenticated.value,
    active: ['profile', 'settings'].includes(route.name),
    action: () => router.push({ name: 'profile' }),
    badge: 0,
  },
  {
    key: 'admin',
    label: copy.ops,
    icon: 'admin',
    show: isAuthenticated.value && isAdmin.value,
    active: route.name === 'admin',
    action: () => router.push({ name: 'admin' }),
    badge: 0,
  },
].filter((item) => item.show))

// 移动端底部 Tab（< md 固定 5 个）：大厅 · 订单 · 钱包 · 消息 · 我的
const mobileTabItems = computed(() => [
  {
    key: 'hall',
    label: copy.hall,
    to: { name: 'home' },
    active: ['home', 'order-detail'].includes(route.name),
    badge: 0,
    icon: 'hall',
  },
  {
    key: 'orders',
    label: copy.myOrders,
    to: { name: 'orders' },
    active: ['orders', 'order-create'].includes(route.name),
    badge: 0,
    icon: 'orders',
  },
  {
    key: 'wallet',
    label: copy.wallet,
    to: { name: 'wallet' },
    active: route.name === 'wallet',
    badge: 0,
    icon: 'wallet',
  },
  {
    key: 'messages',
    label: copy.messages,
    to: { name: 'message-center' },
    active: ['message-center', 'chat-detail'].includes(route.name),
    badge: messageBadge.value,
    icon: 'messages',
  },
  {
    key: 'mine',
    label: copy.mine,
    to: { name: 'profile' },
    active: ['profile', 'settings'].includes(route.name),
    badge: 0,
    icon: 'profile',
  },
])

const hideFooter = computed(() => ['login', 'register', 'message-center', 'chat-detail', 'settings', 'support'].includes(route.name))

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
    await settingsStore.fetchPreferences()
    await chatStore.fetchUnreadSummary()
    await notificationsStore.fetchUnreadCount()
    chatStore.connectWebSocket()
    startUnreadPolling()
    startNotifPolling()
    return
  }

  stopUnreadPolling()
  stopNotifPolling()
  knownNotifIds = null
  chatStore.disconnectWebSocket({ clearState: true })
  notificationsStore.resetState()
}

async function handleLogout() {
  stopUnreadPolling()
  stopNotifPolling()
  knownNotifIds = null
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
  stopNotifPolling()
  chatStore.disconnectWebSocket()
})
</script>

<template>
  <!-- main-with-tabbar 挂在包住 main+footer 的外层：移动端固定 Tab 栏不再盖住页脚内容 -->
  <div class="min-h-screen" :class="{ 'main-with-tabbar': showTabBar }">
    <nav class="app-header">
      <!-- 桌面顶栏 -->
      <div class="shell-container hidden min-h-16 items-center justify-between gap-4 py-3 md:flex">
        <button type="button" class="brand-lockup text-left" @click="router.push({ name: 'home' })">
          <img v-if="siteLogo" :src="siteLogo" class="brand-mark !h-9 !w-9 object-cover" alt="" /><span v-else class="brand-mark !h-9 !w-9 !text-sm" aria-hidden="true">◆</span>
          <p class="brand-lockup__title text-base text-ink-1">{{ siteName }}</p>
        </button>

        <div class="flex items-center justify-center gap-1">
          <button
            v-for="item in primaryNavItems"
            :key="item.key"
            type="button"
            :class="item.active ? 'app-nav-link app-nav-link-active' : 'app-nav-link'"
            @click="item.action"
          >
            <AppNavIcon :name="item.icon" />
            <span>{{ item.label }}</span>
            <ChatUnreadBadge v-if="item.badge" :count="item.badge" />
          </button>
        </div>

        <div class="flex items-center justify-end gap-2 sm:gap-3">
          <button type="button" class="app-command hidden xl:inline-flex" @click="openSearch">
            {{ copy.searchOrders }}
          </button>
          <button
            type="button"
            class="app-command h-11 w-11 !p-0 xl:hidden"
            title="搜索"
            aria-label="搜索"
            @click="openSearch"
          >
            <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
          </button>

          <ThemeToggle />

          <template v-if="isAuthenticated">
            <span :class="[roleMeta.badgeClass, 'hidden lg:inline-flex']">{{ roleMeta.label }}</span>
            <router-link to="/profile" class="btn-secondary !px-4 hidden lg:inline-flex">{{ user?.username || copy.profile }}</router-link>
            <router-link to="/settings" class="btn-ghost !px-3 hidden lg:inline-flex" title="设置">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clip-rule="evenodd" />
              </svg>
            </router-link>
            <button class="btn-ghost !px-4 hidden lg:inline-flex" @click="handleLogout">{{ copy.logout }}</button>
            <!-- md–lg 折叠为头像（完整用户区在 lg+ 展开；退出/设置在“我的”页可达） -->
            <router-link
              to="/profile"
              class="flex h-11 w-11 items-center justify-center rounded-full bg-primary-soft text-sm font-semibold text-primary lg:hidden"
              :title="user?.username || copy.profile"
              aria-label="个人中心"
            >
              {{ avatarText }}
            </router-link>
          </template>
          <template v-else>
            <router-link to="/login" class="btn-ghost !px-4">{{ copy.login }}</router-link>
            <router-link to="/register" class="btn-primary !px-4 hidden sm:inline-flex">{{ copy.signup }}</router-link>
          </template>
        </div>
      </div>

      <!-- 移动端顶栏：品牌 logo + 搜索 + 主题切换 + 用户头像 -->
      <div class="shell-container flex min-h-14 items-center justify-between gap-2 py-2 md:hidden">
        <button type="button" class="brand-lockup" @click="router.push({ name: 'home' })">
          <img v-if="siteLogo" :src="siteLogo" class="brand-mark !h-9 !w-9 object-cover" alt="" /><span v-else class="brand-mark !h-9 !w-9 !text-sm" aria-hidden="true">◆</span>
        </button>

        <div class="flex items-center gap-1">
          <button
            type="button"
            class="inline-flex h-11 w-11 items-center justify-center rounded-full text-ink-2 transition-colors duration-base hover:bg-surface-3 hover:text-ink-1"
            title="搜索"
            aria-label="搜索"
            @click="openSearch"
          >
            <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
          </button>

          <ThemeToggle />

          <router-link
            v-if="isAuthenticated"
            to="/profile"
            class="flex h-11 w-11 items-center justify-center rounded-full bg-primary-soft text-sm font-semibold text-primary"
            :title="user?.username || copy.profile"
            aria-label="个人中心"
          >
            {{ avatarText }}
          </router-link>
          <router-link
            v-else
            to="/login"
            class="btn-primary !px-4"
          >
            {{ copy.login }}
          </router-link>
        </div>
      </div>
    </nav>

    <!-- 不设 z-10：main 一旦形成堆叠上下文，页内弹窗 z-50 会被根级 z-40 的
         底部 Tab 栏盖住（按钮点不到）。保持 z-index auto 让弹窗全局置顶。 -->
    <main class="relative">
      <router-view v-slot="{ Component }">
        <transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>

      <!-- 网页内新消息推送 toast：有新消息才弹，自动消失，可点 X 关；点击内容直达会话 -->
      <teleport to="body">
        <div class="pointer-events-none fixed left-1/2 top-4 z-[60] flex w-[calc(100vw-2rem)] max-w-sm -translate-x-1/2 flex-col gap-2">
          <transition-group name="page-fade">
            <div
              v-for="toast in toastsStore.toasts"
              :key="toast.id"
              class="pointer-events-auto flex w-full items-start gap-2 rounded-card border border-line-1 bg-surface px-4 py-3 shadow-lg"
            >
              <button
                type="button"
                class="flex min-w-0 flex-1 flex-col text-left"
                @click="handleToastClick(toast)"
              >
                <span class="truncate text-sm font-semibold text-ink-1">{{ toast.title }}</span>
                <span v-if="toast.body" class="mt-0.5 truncate text-xs text-ink-2">{{ toast.body }}</span>
              </button>
              <button
                type="button"
                class="shrink-0 rounded-full p-1 text-ink-3 transition-colors duration-base hover:bg-surface-3 hover:text-ink-1"
                aria-label="关闭提示"
                @click.stop="toastsStore.dismissToast(toast.id)"
              >
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
          </transition-group>
        </div>
      </teleport>
    </main>

    <footer v-if="!hideFooter" class="mt-4 border-t border-line-1 bg-surface/60">
      <div class="shell-container flex flex-col gap-8 py-10 lg:flex-row lg:items-start lg:justify-between">
        <div class="max-w-sm space-y-3">
          <div class="brand-lockup">
            <img v-if="siteLogo" :src="siteLogo" class="brand-mark !h-8 !w-8 object-cover" alt="" /><span v-else class="brand-mark !h-8 !w-8 !text-xs" aria-hidden="true">◆</span>
            <p class="brand-lockup__title text-sm text-ink-1">{{ siteName }}</p>
          </div>
          <p class="text-sm leading-7 text-ink-3">{{ copy.footerLine }}</p>
        </div>

        <div class="grid gap-8 sm:grid-cols-2">
          <div class="space-y-3">
            <p class="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">导航</p>
            <div class="flex flex-col items-start gap-2.5">
              <router-link to="/search?type=all" class="text-sm text-ink-2 transition-colors duration-base hover:text-primary">{{ copy.advancedSearch }}</router-link>
              <router-link v-if="isAuthenticated" to="/orders" class="text-sm text-ink-2 transition-colors duration-base hover:text-primary">{{ copy.myOrders }}</router-link>
              <router-link to="/games" class="text-sm text-ink-2 transition-colors duration-base hover:text-primary">{{ copy.games }}专区</router-link>
            </div>
          </div>
          <div class="space-y-3">
            <p class="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">服务</p>
            <div class="flex flex-col items-start gap-2.5">
              <router-link v-if="isAuthenticated && !isAdmin" to="/services" class="text-sm text-ink-2 transition-colors duration-base hover:text-primary">{{ copy.boosterDesk }}</router-link>
              <router-link v-if="isAuthenticated" to="/support" class="text-sm text-ink-2 transition-colors duration-base hover:text-primary">联系管理员</router-link>
            </div>
          </div>
        </div>
      </div>
    </footer>

    <!-- 移动端固定底部 Tab：< md 显示，5 个（大厅/订单/钱包/消息/我的） -->
    <nav v-if="showTabBar" class="app-tabbar" aria-label="移动端主导航">
      <router-link
        v-for="item in mobileTabItems"
        :key="item.key"
        :to="item.to"
        :class="item.active ? 'app-tabbar__item app-tabbar__item-active' : 'app-tabbar__item'"
      >
        <span class="relative">
          <AppNavIcon :name="item.icon" />
          <span v-if="item.badge" class="tabbar-badge">{{ item.badge > 99 ? '99+' : item.badge }}</span>
        </span>
        <span class="app-tabbar__label">{{ item.label }}</span>
      </router-link>
    </nav>
  </div>
</template>
