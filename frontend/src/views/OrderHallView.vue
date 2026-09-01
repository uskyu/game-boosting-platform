<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useOrdersStore } from '@/stores/orders'
import { formatCount, formatPrice, formatShortDate } from '@/utils/display'
import { ORDER_STATUS_OPTIONS, getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'
import { getGameImage } from '@/data/gameImages'

/**
 * 订单大厅（IA v2：/ = 产品心脏）。
 * 顶部统计条（待接单 / 进行中 / 今日完成，大数字）→ 游戏/状态筛选一行
 * → 订单卡网格（桌面 2 列、移动 1 列）→ 空态。
 * 卡片信息层级（文档 6 节）：价格最大最显眼 → 游戏名 + 状态标签
 * → 目标段位/时间 → 打手视角主 CTA「立即抢单」。
 */
const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const ordersStore = useOrdersStore()

const searchGame = ref('')
const selectedStatus = ref('')

const orders = computed(() => ordersStore.orders)
const loading = computed(() => ordersStore.loading)
const pagination = computed(() => ordersStore.pagination)
const error = computed(() => ordersStore.error)
const isAuthenticated = computed(() => authStore.isAuthenticated)
const isBooster = computed(() => authStore.isBooster)
const isAdmin = computed(() => authStore.isAdmin)
const currentUserId = computed(() => authStore.user?.id ?? null)

const unreadMap = computed(() => {
  return chatStore.conversations.reduce((result, conversation) => {
    const orderId = Number(conversation.order?.id || conversation.order_id || 0)
    if (!orderId) {
      return result
    }
    result[orderId] = Number(result[orderId] || 0) + Number(conversation.unread_count || 0)
    return result
  }, {})
})

// 顶部统计条：大数字（24px semibold tabular-nums，文档 5 节统计卡标准）
const hallStats = computed(() => [
  { key: 'pending', label: '待接单', value: formatCount(orders.value.filter((item) => item.status === 'PENDING').length), tone: 'text-ink-1' },
  { key: 'locked', label: '进行中', value: formatCount(orders.value.filter((item) => item.status === 'LOCKED').length), tone: 'text-primary' },
  { key: 'done', label: '今日完成', value: formatCount(orders.value.filter((item) => item.status === 'COMPLETED').length), tone: 'text-success' },
])

function getOrderUnreadCount(orderId) {
  return Number(unreadMap.value[orderId] || 0)
}

function gameBadgeStyle(gameName) {
  const { color } = getGameImage(gameName)

  // 游戏主题色为内容资产：品牌色淡底，无发光
  return {
    borderColor: `${color}66`,
    background: `linear-gradient(135deg, ${color}22, ${color}10)`,
  }
}

function buildSummary(order) {
  const detail = order.ai_tags?.detail || {}
  const requirements = Array.isArray(detail.requirements) ? detail.requirements.filter(Boolean) : []
  const pieces = [
    order.service_type,
    order.server,
    detail.role,
    requirements[0],
  ].filter(Boolean)

  if (pieces.length) {
    return pieces.join(' · ')
  }

  const raw = order.description_raw || '未补充需求'
  return raw.length > 28 ? `${raw.slice(0, 28)}...` : raw
}

async function fetchOrders() {
  ordersStore.setFilters({
    gameName: searchGame.value,
    status: selectedStatus.value,
  })
  await ordersStore.fetchOrders()
}

function handleSearch() {
  ordersStore.setPage(1)
  fetchOrders()
}

function resetFilters() {
  searchGame.value = ''
  selectedStatus.value = ''
  ordersStore.setPage(1)
  fetchOrders()
}

function goToOrder(orderId) {
  router.push({ name: 'order-detail', params: { id: orderId } })
}

async function handleAcceptOrder(orderId, event) {
  event.stopPropagation()
  const result = await ordersStore.acceptOrder(orderId)
  if (!result.success) {
    window.alert(result.error)
  }
}

function handlePageChange(page) {
  if (page < 1 || page > pagination.value.pages || page === pagination.value.page) {
    return
  }
  ordersStore.setPage(page)
  fetchOrders()
}

let searchTimeout = null

watch(searchGame, () => {
  window.clearTimeout(searchTimeout)
  searchTimeout = window.setTimeout(() => {
    ordersStore.setPage(1)
    fetchOrders()
  }, 300)
})

watch(isAuthenticated, (loggedIn) => {
  if (loggedIn) {
    fetchOrders()
  }
})

onMounted(async () => {
  if (isAuthenticated.value) {
    fetchOrders()
    await chatStore.fetchConversations({ pageSize: 100 })
    await chatStore.fetchUnreadSummary()
  }
})

onUnmounted(() => {
  window.clearTimeout(searchTimeout)
})
</script>

<template>
  <div class="page-shell space-y-6">
    <!-- 页面标题区：eyebrow → 大标题 → 副文案（文档 3 节） -->
    <section class="hero-panel p-6 sm:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div class="space-y-3">
          <p class="eyebrow">订单大厅</p>
          <h1 class="section-title">挑一单，马上开干</h1>
          <p class="section-copy max-w-xl">
            {{ isBooster ? '实时滚动的平台需求，价格、段位一目了然，看中就抢单。' : '所有正在寻找打手的需求都在这里，点进卡片查看详情。' }}
          </p>
        </div>

        <!-- 顾客视角：发布需求入口（保持不变） -->
        <router-link
          v-if="isAuthenticated && !isBooster && !isAdmin"
          to="/orders/create"
          class="btn-primary shrink-0 self-start px-6"
        >
          发布需求
        </router-link>

        <!-- 管理员视角：去管理台发单（自动切到订单管理 tab） -->
        <router-link
          v-else-if="isAuthenticated && isAdmin"
          :to="{ path: '/admin', query: { tab: 'orders' } }"
          class="btn-primary shrink-0 self-start px-6"
        >
          发布订单
        </router-link>
      </div>

      <!-- 统计条：待接单 / 进行中 / 今日完成（大数字 tabular-nums） -->
      <div class="mt-6 grid grid-cols-3 gap-3 sm:gap-4">
        <article v-for="item in hallStats" :key="item.key" class="stat-card">
          <p class="stat-value" :class="item.tone">{{ item.value }}</p>
          <p class="mt-1.5 text-[13px] text-ink-2">{{ item.label }}</p>
        </article>
      </div>
    </section>

    <!-- 筛选一行：游戏 + 状态（窄屏横向滚动不折行，文档 7 节） -->
    <section class="surface-card p-4 sm:p-5">
      <div class="scroll-x flex flex-nowrap items-end gap-3 lg:flex-wrap lg:items-end">
        <div class="w-56 shrink-0 sm:w-64">
          <label class="label" for="hall-game">游戏</label>
          <input id="hall-game" v-model="searchGame" type="text" class="input" placeholder="搜索游戏" />
        </div>

        <div class="w-36 shrink-0 sm:w-44">
          <label class="label" for="hall-status">状态</label>
          <select id="hall-status" v-model="selectedStatus" class="input" @change="handleSearch">
            <option v-for="option in ORDER_STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </div>

        <div class="flex shrink-0 gap-2 pb-0.5">
          <button class="btn-secondary !px-4" @click="handleSearch">筛选</button>
          <button class="btn-ghost !px-4" @click="resetFilters">重置</button>
        </div>
      </div>
    </section>

    <div v-if="error" class="message-error">{{ error }}</div>

    <!-- 订单卡网格：桌面 2 列、移动 1 列 -->
    <section v-if="loading && isAuthenticated" class="grid gap-4 md:grid-cols-2" aria-busy="true">
      <div v-for="n in 4" :key="`hall-skeleton-${n}`" class="skeleton-row">
        <div class="flex-1 space-y-3">
          <div class="skeleton-line h-6 w-28"></div>
          <div class="skeleton-line h-4 w-2/5"></div>
          <div class="skeleton-line h-3 w-3/5"></div>
        </div>
        <div class="space-y-3">
          <div class="skeleton-line ml-auto h-8 w-24"></div>
          <div class="skeleton-line ml-auto h-9 w-24"></div>
        </div>
      </div>
    </section>

    <section v-else-if="isAuthenticated && orders.length" class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <article
        v-for="order in orders"
        :key="order.id"
        class="catalog-card cursor-pointer"
        @click="goToOrder(order.id)"
      >
        <!-- 层级 1：价格最大最显眼（--price 红 + tabular-nums，24px semibold） -->
        <div class="flex items-start justify-between gap-3">
          <p class="text-2xl font-semibold tabular-nums leading-7 text-price">{{ formatPrice(order.price) }}</p>
          <div class="flex flex-wrap items-center justify-end gap-2">
            <span :class="getOrderStatusBadgeClass(order.status)">
              {{ getOrderStatusLabel(order.status) }}
            </span>
            <span
              v-if="order.payment_status"
              :class="{
                tag: true,
                '!bg-warning-soft !text-warning': order.payment_status === 'UNPAID',
                '!bg-success-soft !text-success': order.payment_status === 'PAID',
                '!bg-surface-3 !text-ink-2': order.payment_status === 'REFUNDED',
              }"
            >
              {{ order.payment_status === 'UNPAID' ? '待支付' : order.payment_status === 'PAID' ? '已支付' : '已退款' }}
            </span>
          </div>
        </div>

        <!-- 层级 2：游戏名 + 需求摘要 -->
        <div class="mt-3 flex items-center gap-3">
          <div
            class="flex h-10 w-10 shrink-0 items-center justify-center rounded-tile border text-sm font-semibold text-ink-1"
            :style="gameBadgeStyle(order.game_name)"
          >
            {{ order.game_name?.slice(0, 1) || '?' }}
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="text-[17px] font-semibold leading-6 text-ink-1">{{ order.game_name }}</h2>
              <span v-if="getOrderUnreadCount(order.id)" class="tag !bg-warning-soft !text-warning">
                消息 {{ getOrderUnreadCount(order.id) }}
              </span>
            </div>
            <p class="mt-1 truncate text-[13px] text-ink-2">{{ buildSummary(order) }}</p>
          </div>
        </div>

        <!-- 层级 3：目标段位 / 时间 -->
        <div class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-ink-2">
          <span class="tabular-nums">{{ order.current_rank }} → {{ order.target_rank }}</span>
          <span class="text-ink-3">发布于 {{ formatShortDate(order.created_at) }}</span>
        </div>

        <!-- 层级 4：打手视角主 CTA「立即抢单」/ 顾客视角详情入口 -->
        <div class="mt-4 flex items-center justify-between gap-3 border-t border-line-1 pt-3.5">
          <p class="text-xs tabular-nums text-ink-3">#{{ order.id }}</p>
          <div class="flex gap-2">
            <button
              v-if="isBooster && order.status === 'PENDING' && order.user_id !== currentUserId"
              class="btn-primary !px-5"
              @click="handleAcceptOrder(order.id, $event)"
            >
              立即抢单
            </button>
            <button
              v-else-if="!isBooster || order.status !== 'PENDING' || order.user_id === currentUserId"
              class="btn-secondary !px-4"
              @click.stop="goToOrder(order.id)"
            >
              查看详情
            </button>
          </div>
        </div>
      </article>
    </section>

    <!-- 空态：登录引导（大厅订单仅登录可见，GET /orders 需要登录） / 无订单 -->
    <section v-else-if="!isAuthenticated" class="surface-card">
      <div class="empty-state py-16">
        <div class="empty-state__icon" aria-hidden="true">
          <svg class="h-8 w-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <rect x="4" y="10.5" width="16" height="9.5" rx="2.5" />
            <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
          </svg>
        </div>
        <h2 class="empty-state__title">登录后查看可抢订单</h2>
        <p class="empty-state__copy">大厅的实时订单仅对平台用户开放：登录后即可浏览全部待接订单，打手可直接抢单。</p>
        <div class="mt-5 flex gap-3">
          <router-link to="/login" class="btn-primary !px-6">登录</router-link>
          <router-link to="/register" class="btn-secondary !px-6">注册</router-link>
        </div>
      </div>
    </section>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🗂️</div>
      <h2 class="empty-state__title">暂无匹配的订单</h2>
      <p class="empty-state__copy">换个筛选条件试试，或者稍后回来看看新需求。</p>
    </section>

    <section v-if="pagination.pages > 1" class="surface-card p-4 sm:p-5">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm tabular-nums text-ink-2">
          {{ pagination.page }} / {{ pagination.pages }} · 共 {{ formatCount(pagination.total) }} 单
        </p>

        <div class="flex items-center gap-2">
          <button class="btn-secondary !px-4" :disabled="pagination.page <= 1" @click="handlePageChange(pagination.page - 1)">上一页</button>
          <button class="btn-secondary !px-4" :disabled="pagination.page >= pagination.pages" @click="handlePageChange(pagination.page + 1)">下一页</button>
        </div>
      </div>
    </section>
  </div>
</template>
