<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useOrdersStore } from '@/stores/orders'
import { formatCount, formatPayoutDelay, formatPrice, formatShortDate } from '@/utils/display'
import { ORDER_STATUS_OPTIONS, getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'

/**
 * 订单大厅（IA v2：/ = 产品心脏）。
 * 顶部统计条（待接单 / 进行中 / 今日完成，大数字）→ 游戏/状态筛选一行
 * → 订单卡网格（桌面 2 列、移动 1 列）→ 空态。
 * 卡片信息层级（减法）：价格最大最显眼 → 当前情况 X/Y
 * → 炸单赔偿 / 到账时效 chips → 底部次要信息行（需求摘要 · 游戏名 · 时间 · #id）+「查看详情 →」。
 * 接单两步走：整卡点击进详情，详情页内确认接单。
 * 挂载后每 30 秒静默刷新当前页（页面可见时），检测到新订单弹轻提示。
 */
const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const ordersStore = useOrdersStore()

const searchGame = ref('')
const selectedStatus = ref('')
const showHistory = ref(false)
// The hall starts in claimable-only mode so stale dispatch records are not
// presented as actionable orders before the user changes the filter.
const openOnly = ref(true)

const orders = computed(() => ordersStore.orders)
const terminalStatuses = ['COMPLETED', 'CANCELLED', 'EXPIRED', 'ARCHIVED']

function isOrderClaimable(order) {
  if (!order || !['PENDING', 'LOCKED'].includes(order.status) || order.claim_status !== 'OPEN' || order.is_archived) return false
  if (Number(order.claimed_count ?? 0) >= Number(order.max_claims ?? 0)) return false
  if (!order.deadline) return true
  const deadline = new Date(order.deadline)
  return !Number.isNaN(deadline.getTime()) && deadline.getTime() > Date.now()
}

const visibleOrders = computed(() => orders.value.filter((order) => {
  if (showHistory.value) return true
  // "Only claimable" is the safe default for the public hall. Non-claimable
  // orders remain available through the dedicated history/my-orders views.
  if (openOnly.value) return isOrderClaimable(order)
  return !terminalStatuses.includes(order.status)
}))

function getOrderDisplayStatus(order) {
  if (order.is_archived) return '已归档'
  if (order.claim_status === 'PAUSED') return '暂停接单'
  if (order.claim_status === 'FULL' || Number(order.claimed_count ?? 0) >= Number(order.max_claims ?? 0)) return '已满员'
  if (order.claim_status === 'CLOSED') return '已截止'
  if (order.deadline && new Date(order.deadline).getTime() <= Date.now()) return '已截止'
  return getOrderStatusLabel(order.status)
}

function getOrderDisplayBadgeClass(order) {
  if (order.is_archived || order.claim_status === 'CLOSED' || (order.deadline && new Date(order.deadline).getTime() <= Date.now())) return 'badge-cancelled'
  if (order.claim_status === 'PAUSED' || order.claim_status === 'FULL') return 'badge-review'
  return getOrderStatusBadgeClass(order.status)
}
const loading = computed(() => ordersStore.loading)
const pagination = computed(() => ordersStore.pagination)
const error = computed(() => ordersStore.error)
const isAuthenticated = computed(() => authStore.isAuthenticated)
const isAdmin = computed(() => authStore.isAdmin)
const gameOptions = computed(() => [...new Set(orders.value.map((order) => order.game_name).filter(Boolean))])

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

function getPriceLabel(order) {
  // 固定价展示：只显示 ¥price（区间价格已下线）
  return formatPrice(order?.price)
}

function getClaimMeta(order) {
  const claimed = Number(order.claimed_count ?? order.accepted_count ?? 0)
  const max = Number(order.max_claims ?? order.max_boosters ?? 0)
  const safeClaimed = Number.isNaN(claimed) ? 0 : claimed
  const safeMax = Number.isNaN(max) ? 0 : max
  return { claimed: safeClaimed, max: safeMax, remaining: safeMax ? Math.max(0, safeMax - safeClaimed) : null }
}

function getAttachment(order) {
  const attachments = order.attachments || order.attachment_urls || []
  const first = Array.isArray(attachments) ? attachments[0] : attachments
  const url = typeof first === 'string' ? first : first?.url || ''
  return typeof url === 'string' && url.startsWith('/uploads/orders/') ? url : ''
}

function getMetaLine(order) {
  const pieces = []
  if (order.compensation_amount) pieces.push(`炸单赔偿 ${formatPrice(order.compensation_amount)}`)
  const payoutText = formatPayoutDelay(order)
  if (payoutText) pieces.push(`${payoutText}到账`)
  const { claimed, max } = getClaimMeta(order)
  if (max > 0) pieces.push(`当前情况 ${claimed}/${max}`)
  return pieces.join(' · ')
}

function isFullOrder(order) {
  const { claimed, max } = getClaimMeta(order)
  if (order.claim_status === 'FULL') return true
  if (max > 0 && claimed >= max) return true
  return false
}

function buildSummary(order) {
  if (order.intro) {
    return order.intro.length > 28 ? `${order.intro.slice(0, 28)}...` : order.intro
  }
  const detail = order.ai_tags?.detail || {}
  const requirements = Array.isArray(detail.requirements) ? detail.requirements.filter(Boolean) : []
  const pieces = [
    order.service_type,
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

// 大厅自动刷新：每 8 秒页面可见时静默拉取；首屏出现新单时轻提示（不弹骨架、不清列表）
const HALL_REFRESH_INTERVAL = 8_000
const newOrderTip = ref(false)
let hallRefreshTimer = null
let knownFirstPageIds = null
let newOrderTipTimer = null

async function silentRefresh() {
  if (document.visibilityState !== 'visible' || !isAuthenticated.value) return
  const previousIds = knownFirstPageIds
  try {
    await ordersStore.fetchOrders({ silent: true })
  } catch {
    return // 静默失败等下一轮
  }
  const currentIds = ordersStore.orders.slice(0, 20).map((o) => o.id)
  knownFirstPageIds = currentIds
  if (previousIds && currentIds.length && currentIds.some((id) => !previousIds.includes(id))) {
    newOrderTip.value = true
    // 大厅仅保留顶部提示，toast 和声音统一由全站通知处理。
    window.clearTimeout(newOrderTipTimer)
    newOrderTipTimer = window.setTimeout(() => { newOrderTip.value = false }, 3000)
  }
}

onMounted(async () => {
  if (isAuthenticated.value) {
    fetchOrders()
    await chatStore.fetchConversations({ pageSize: 100 })
    await chatStore.fetchUnreadSummary()
    knownFirstPageIds = ordersStore.orders.slice(0, 20).map((o) => o.id)
    hallRefreshTimer = window.setInterval(silentRefresh, HALL_REFRESH_INTERVAL)
  }
})

onUnmounted(() => {
  window.clearTimeout(searchTimeout)
  window.clearInterval(hallRefreshTimer)
  window.clearTimeout(newOrderTipTimer)
})
</script>

<template>
  <div class="page-shell order-hall-shell space-y-4 sm:space-y-5">
    <!-- 发布按钮与统计卡同排：节省纵向空间（统计卡可横向滚动，按钮固定右侧） -->
    <section class="flex items-stretch gap-2 sm:gap-3">
      <div class="hall-summary min-w-0 flex-1" aria-label="大厅统计">
        <article v-for="item in hallStats" :key="item.key" class="hall-summary__item"><strong :class="item.tone">{{ item.value }}</strong><span>{{ item.label }}</span></article>
      </div>
      <router-link v-if="isAuthenticated" :to="{ name: 'order-create' }" class="btn-primary flex shrink-0 items-center !px-5">发布订单</router-link>
    </section>

    <!-- 自动刷新捕获到新单时的轻提示（3 秒自动消失） -->
    <transition name="page-fade">
      <p
        v-if="newOrderTip"
        class="fixed left-1/2 top-20 z-50 -translate-x-1/2 rounded-full bg-ink-1 px-4 py-2 text-sm font-medium text-surface shadow-lg"
        role="status"
      >🔔 有新订单发布</p>
    </transition>

    <!-- 筛选一行：游戏 + 状态 + 复选框 + 按钮同一行，窄屏横向滚动不折行（文档 7 节） -->
    <section class="surface-card p-4 sm:p-5">
      <div class="hall-filters scroll-x flex flex-nowrap items-center gap-3">
        <div class="w-56 shrink-0 sm:w-64">
          <label class="label" for="hall-game">游戏</label>
          <input id="hall-game" v-model="searchGame" list="hall-games" type="text" class="input h-11" placeholder="搜索游戏" />
          <datalist id="hall-games"><option v-for="game in gameOptions" :key="game" :value="game" /></datalist>
        </div>

        <div class="w-36 shrink-0 sm:w-44">
          <label class="label" for="hall-status">状态</label>
          <select id="hall-status" v-model="selectedStatus" class="input h-11" @change="handleSearch">
            <option v-for="option in ORDER_STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </div>

        <div class="flex shrink-0 items-center gap-2">
          <label class="filter-check"><input v-model="openOnly" type="checkbox" /> 仅看可抢</label>
          <label class="filter-check"><input v-model="showHistory" type="checkbox" /> 历史订单</label>
          <button class="btn-secondary shrink-0 !px-4" @click="handleSearch">筛选</button><button class="btn-ghost shrink-0 !px-4" @click="resetFilters">重置</button>
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

    <section v-else-if="isAuthenticated && visibleOrders.length" class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <article
        v-for="order in visibleOrders"
        :key="order.id"
        :class="['catalog-card cursor-pointer hall-order-card', { 'hall-order-card--full': isFullOrder(order) }]"
        @click="goToOrder(order.id)"
      >
        <!-- 层级：价格最大红字 → 标题 → 一行小字元信息 → 底部次要行 -->
        <div class="flex items-start justify-between gap-2">
          <p class="shrink-0 text-2xl font-semibold tabular-nums leading-7 text-price">{{ getPriceLabel(order) }}</p>
          <span v-if="isFullOrder(order)" class="badge-cancelled shrink-0">已抢空</span>
          <span v-else-if="!isOrderClaimable(order)" :class="[getOrderDisplayBadgeClass(order), 'shrink-0']">{{ getOrderDisplayStatus(order) }}</span>
        </div>

        <!-- 标题 -->
        <p v-if="order.title" class="mt-3 truncate text-[15px] font-semibold text-ink-1">{{ order.title }}</p>

        <!-- 炸单赔偿 / 到账时效 / 当前情况收敛为一行 13px 小字 -->
        <p v-if="getMetaLine(order)" class="mt-1.5 truncate text-[13px] tabular-nums text-ink-2">{{ getMetaLine(order) }}</p>

        <div v-if="getAttachment(order)" class="mt-3 overflow-hidden rounded-tile"><img :src="getAttachment(order)" alt="订单附件" loading="lazy" class="max-h-40 w-full rounded object-cover" /></div>

        <!-- 层级 3：底部次要信息行（需求摘要 + 游戏名 + 时间）与「查看详情 →」入口 -->
        <div class="mt-4 border-t border-line-1 pt-3.5 text-[13px]">
          <p class="truncate text-ink-2">{{ buildSummary(order) }}</p>
          <div class="mt-2 flex items-center justify-between gap-3">
            <div class="flex min-w-0 items-center gap-2 text-ink-2">
              <span class="truncate font-medium">{{ order.game_name }}</span>
              <span v-if="getOrderUnreadCount(order.id)" class="tag shrink-0 !bg-warning-soft !text-warning">
                消息 {{ getOrderUnreadCount(order.id) }}
              </span>
              <span class="shrink-0 text-ink-3">发布于 {{ formatShortDate(order.created_at) }}</span>
              <span class="shrink-0 tabular-nums text-ink-3">#{{ order.id }}</span>
            </div>
            <button
              type="button"
              class="btn-ghost shrink-0 !min-h-[36px] !px-4 !py-1.5"
              @click.stop="goToOrder(order.id)"
            >
              查看详情 →
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
