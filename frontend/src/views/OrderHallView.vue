<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useNotificationsStore } from '@/stores/notifications'
import { useOrdersStore } from '@/stores/orders'
import { useSiteStore } from '@/stores/site'
import { formatCount, formatPayoutDelay, formatPrice, formatShortDate, getAcceptWaitMeta, serverNow } from '@/utils/display'
import { ORDER_STATUS_OPTIONS, getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'
import api from '@/utils/api'

/**
 * 订单大厅（IA v2：/ = 产品心脏）。
 * 顶部统计条（待接单 / 进行中 / 今日完成，大数字）→ 状态筛选一行
 * → 订单卡网格（桌面 2 列、移动 1 列）→ 空态 → 今日已接单区块。
 * 卡片信息层级（减法）：价格最大最显眼 → 当前情况 X/Y
 * → 炸单赔偿 / 到账时效 chips → 底部次要信息行（需求摘要 · 游戏名 · 时间 · #id）+「查看详情 →」。
 * 接单两步走：整卡点击进详情，详情页内确认接单。
 * 挂载后静默刷新当前页（页面可见时），检测到新订单弹轻提示。
 */
const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const notificationsStore = useNotificationsStore()
const ordersStore = useOrdersStore()
const siteStore = useSiteStore()

const selectedStatus = ref('')
const showHistory = ref(false)
// The hall starts in claimable-only mode so stale dispatch records are not
// presented as actionable orders before the user changes the filter.
const openOnly = ref(true)

// 抢单倒计时：每秒刷新 now，与 accept_available_at 求差得到剩余等待秒数
// now 必须用服务器校准时间（serverNow），设备本地时钟偏差会误导显示
const now = ref(serverNow())

function getAcceptWaitMetaFor(order) {
  if (!isOrderClaimable(order) || order.my_claim) {
    return { remaining: 0, total: 0, state: 'available' }
  }
  return getAcceptWaitMeta(order, now.value)
}

function getAcceptWaitLabel(order) {
  const { remaining, total } = getAcceptWaitMetaFor(order)
  if (remaining <= 0) return ''
  return total > 0
    ? `接单等待：剩余 ${remaining} 秒（配置等待 ${total} 秒）`
    : `接单等待：剩余 ${remaining} 秒`
}

const orders = computed(() => ordersStore.orders)
const terminalStatuses = ['COMPLETED', 'CANCELLED', 'EXPIRED', 'ARCHIVED']

function isOrderClaimable(order) {
  if (!order || !['PENDING', 'LOCKED'].includes(order.status) || order.claim_status !== 'OPEN' || order.is_archived) return false
  if (Number(order.claimed_count ?? 0) >= Number(order.max_claims ?? 0)) return false
  if (!order.deadline) return true
  const deadline = new Date(order.deadline)
  return !Number.isNaN(deadline.getTime()) && deadline.getTime() > serverNow()
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
  if (order.deadline && new Date(order.deadline).getTime() <= serverNow()) return '已截止'
  return getOrderStatusLabel(order.status)
}

function getOrderDisplayBadgeClass(order) {
  if (order.is_archived || order.claim_status === 'CLOSED' || (order.deadline && new Date(order.deadline).getTime() <= serverNow())) return 'badge-cancelled'
  if (order.claim_status === 'PAUSED' || order.claim_status === 'FULL') return 'badge-review'
  return getOrderStatusBadgeClass(order.status)
}
const loading = computed(() => ordersStore.loading)
const error = computed(() => ordersStore.error)
const isAuthenticated = computed(() => authStore.isAuthenticated)
const isAdmin = computed(() => authStore.isAdmin)

// ── 今日已接单（大厅底部区块） ──
// 全站开关：管理员在站点设置里控制所有人可见性（默认开）；开关关闭或未登录时不渲染。
// 数据：GET /orders/recent-claims（登录可用），挂载拉一次 + silentRefresh 节流兜底。
// 断线时大厅轮询压到 2s，这里至少 15s 才刷一次，避免双倍请求；失败静默不打扰大厅。
const hallRecentClaimsVisible = computed(
  () => siteStore.settings.hall_recent_claims_enabled !== false && isAuthenticated.value,
)
// 收起时完全隐藏列表，只保留标题/查看按钮；查询 1 条即可拿 total。
// 查看后最多展示 100 条，避免把“收起”做成仍残留多行的假收起。
const RECENT_CLAIMS_COLLAPSED_LIMIT = 1
const RECENT_CLAIMS_MAX = 100
const RECENT_CLAIMS_REFRESH_INTERVAL = 15_000
const recentClaims = ref([])
const recentClaimsTotal = ref(0)
const recentClaimsExpanded = ref(false)
const recentClaimsLoading = ref(false)
let lastRecentClaimsRefreshAt = 0

const visibleRecentClaims = computed(() => (
  recentClaimsExpanded.value ? recentClaims.value : []
))
const recentClaimsCanExpand = computed(() => recentClaimsTotal.value > 0)

function recentClaimsThrottleReady() {
  const nowTs = Date.now()
  if (nowTs - lastRecentClaimsRefreshAt < RECENT_CLAIMS_REFRESH_INTERVAL) return false
  lastRecentClaimsRefreshAt = nowTs
  return true
}

async function fetchRecentClaims(limit = RECENT_CLAIMS_COLLAPSED_LIMIT) {
  if (!hallRecentClaimsVisible.value) return
  recentClaimsLoading.value = true
  try {
    const response = await api.get('/orders/recent-claims', {
      params: { scope: 'today', limit },
      timeout: 10000,
    })
    recentClaims.value = response.data?.items ?? []
    recentClaimsTotal.value = Number(response.data?.total ?? 0)
  } catch {
    // 失败静默：区块是附加信息，下一轮兜底刷新再试
  } finally {
    recentClaimsLoading.value = false
  }
}

// 「查看」展开完整列表（最多 100 条）；「收起」后列表完全隐藏，只保留标题栏。
async function toggleRecentClaims() {
  if (recentClaimsExpanded.value) {
    recentClaimsExpanded.value = false
    return
  }
  recentClaimsExpanded.value = true
  if (recentClaims.value.length < recentClaimsTotal.value && recentClaims.value.length < RECENT_CLAIMS_MAX) {
    await fetchRecentClaims(RECENT_CLAIMS_MAX)
  }
}

function getRecentClaimMeta(claim) {
  // 简介选填，没填就整段省略——占位文案「未补充需求」会被用户当成异常数据
  const pieces = [claim.booster.username, `已完成 ${formatCount(claim.booster.total_completed)} 单`]
  if (claim.order.intro) {
    pieces.push(claim.order.intro.length > 40 ? `${claim.order.intro.slice(0, 40)}...` : claim.order.intro)
  }
  pieces.push(formatShortDate(claim.created_at))
  return pieces.join(' · ')
}

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

  const raw = order.description_raw || ''
  return raw.length > 28 ? `${raw.slice(0, 28)}...` : raw
}

async function fetchOrders() {
  ordersStore.setFilters({
    // 大厅与“我的派单”共用 Pinia 筛选状态，切换页面时必须清掉另一页的游戏/老板筛选。
    gameName: '',
    bossContact: '',
    status: selectedStatus.value,
  })
  // 底部分页控件已下线：可抢订单集合很小，一次性拉全（pageSize=100，后端上限），
  // 省掉翻页状态与额外请求；状态/仅看可抢/历史订单筛选仍走同一接口。
  await ordersStore.fetchOrders({ slim: true, page: 1, pageSize: 100 })
}

function handleSearch() {
  fetchOrders()
}

function resetFilters() {
  selectedStatus.value = ''
  openOnly.value = true
  showHistory.value = false
  handleSearch()
}

function goToOrder(orderId) {
  router.push({ name: 'order-detail', params: { id: orderId } })
}

watch(isAuthenticated, (loggedIn) => {
  if (loggedIn) {
    startHallLifecycle()
  }
})

// 大厅以 WebSocket 新订单/订单状态事件为主，收到事件后立即同步；
// 轮询只作为断线、事件丢失或移动端 WS 被系统挂起时的兜底。
// 移动端注意：息屏/切后台时浏览器会冻结定时器并挂断 WebSocket，微信内置
// 浏览器也可能拦截 WSS——只靠长间隔对账会让手机用户比桌面用户晚几十秒
// 才看到新订单（老板实测「电脑端没延迟、手机端有延迟」，以及 WS 断时
// 「有提示音但不出单」）。因此对账间隔压到秒级，WS 不可用更短。
// 成本侧：slim 响应 + noload 查询后单次列表请求 ~30ms，秒级轮询可承受。
// 2026-09-27：WS 兜底从 5s 收到 2s。手机端长连接反复被冻结/杀掉后大部分时间
// 走的就是这条兜底，5s 就是老板实测的「起码五秒」来源；2s 把最坏延迟压到 2s。
const HALL_RECONCILE_INTERVAL = 10_000
const HALL_FALLBACK_INTERVAL = 2_000
let hallRefreshTimer = null
let hallUnmounted = false
// 抢单倒计时：独立 1 秒计时器，仅驱动 now 变化
let countdownTimer = null
// 合并刷新期间到达的通知，当前请求结束后补拉一次，避免「有声音但没新单」
//（通知恰好撞上轮询在飞时，旧版直接丢弃刷新信号）。
let hallRefreshQueued = false
// 在飞保护：弱网下一轮没跑完就不开新一轮，避免请求堆积占满浏览器连接
let hallRefreshing = false

function hallPollInterval() {
  return chatStore.socketStatus === 'connected'
    ? HALL_RECONCILE_INTERVAL
    : HALL_FALLBACK_INTERVAL
}

function restartHallTimer() {
  if (hallUnmounted || !isAuthenticated.value) return
  if (hallRefreshTimer) {
    window.clearInterval(hallRefreshTimer)
    hallRefreshTimer = null
  }
  hallRefreshTimer = window.setInterval(silentRefresh, hallPollInterval())
}

// 页面重新可见（手机解锁/切回应用）：先校准倒计时基准，再立刻同步一次，
// 不让用户对着刚解冻的陈旧页面干等下一个轮询周期。
function handleHallVisibility() {
  if (hallUnmounted || document.visibilityState !== 'visible') return
  now.value = serverNow()
  restartHallTimer()
  silentRefresh().catch(() => {})
}

async function silentRefresh() {
  if (hallUnmounted || !isAuthenticated.value || document.visibilityState !== 'visible') return
  if (hallRefreshing) {
    hallRefreshQueued = true
    return
  }
  hallRefreshing = true
  try {
    await ordersStore.fetchOrders({ silent: true, slim: true, page: 1, pageSize: 100 })
    // 「今日已接单」兜底刷新：与订单同源节流（≥15s），失败静默
    if (hallRecentClaimsVisible.value && recentClaimsThrottleReady()) {
      fetchRecentClaims(recentClaimsExpanded.value ? RECENT_CLAIMS_MAX : RECENT_CLAIMS_COLLAPSED_LIMIT).catch(() => {})
    }
  } catch {
    // 静默失败等下一轮
  } finally {
    hallRefreshing = false
    if (hallRefreshQueued && !hallUnmounted) {
      hallRefreshQueued = false
      window.setTimeout(() => silentRefresh().catch(() => {}), 0)
    }
  }
}

// 新订单通知到达时立即更新可见大厅；若已有刷新在飞，排队补一次。
watch(() => notificationsStore.newOrderNotificationVersion, () => {
  silentRefresh().catch(() => {})
})

// 接单、取消、派单、抢单控制等状态变化会广播给所有在线大厅，
// 避免其他打手继续看到已经失效的订单卡片。
watch(() => chatStore.lastOrderStateChange, (change) => {
  ordersStore.applyOrderStateChange(change)
  silentRefresh().catch(() => {})
})

// 重连成功后补一次同步，覆盖断线期间错过的状态事件。
// 同时按 WS 状态切换轮询节奏：断了走 5 秒短兜底，恢复后回到 30 秒对账。
watch(() => chatStore.socketStatus, (status, previousStatus) => {
  if (status === 'connected' && previousStatus !== 'connected') {
    silentRefresh().catch(() => {})
  }
  restartHallTimer()
})

// 登录后的大厅启动：拉订单、拉站点开关与今日已接单、拉聊天摘要、开自动刷新。
// 身份可能在挂载后才由后台 /auth/me 补全（守卫已不阻塞），挂载和补全两条路径都走这里。
async function startHallLifecycle() {
  if (hallUnmounted) return
  fetchOrders()
  // 站点设置决定「今日已接单」区块是否渲染；App 启动时已预取，这里基本直接命中缓存
  await siteStore.fetchSettings()
  if (hallRecentClaimsVisible.value) {
    lastRecentClaimsRefreshAt = Date.now()
    fetchRecentClaims().catch(() => {})
  }
  // 两个聊天请求互不依赖，并行发出，别串行拖慢大厅。
  // 会话列表只为订单卡片/大厅徽章服务，20 条即可（100 条服务端 ~310ms、
  // 20 条 ~76ms，跨境链路上白拿一截首屏时间）。
  await Promise.allSettled([
    chatStore.fetchConversations({ pageSize: 20 }),
    chatStore.fetchUnreadSummary(),
  ])
  // 页面可能在上面的弱网请求完成前已被卸载；卸载后绝不能复活大厅轮询，
  // 否则它会在“我的派单”继续拉全量订单并覆盖搜索结果。
  restartHallTimer()
}

onMounted(() => {
  hallUnmounted = false
  if (!countdownTimer) {
    countdownTimer = window.setInterval(() => {
      now.value = serverNow()
    }, 1000)
  }
  document.addEventListener('visibilitychange', handleHallVisibility)
  window.addEventListener('focus', handleHallVisibility)
  if (isAuthenticated.value) {
    startHallLifecycle()
  }
})

onUnmounted(() => {
  hallUnmounted = true
  if (countdownTimer) {
    window.clearInterval(countdownTimer)
    countdownTimer = null
  }
  document.removeEventListener('visibilitychange', handleHallVisibility)
  window.removeEventListener('focus', handleHallVisibility)
  if (hallRefreshTimer) {
    window.clearInterval(hallRefreshTimer)
    hallRefreshTimer = null
  }
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

    <!-- 手机单列，桌面紧凑横排并允许空间不足时回流。 -->

    <section class="surface-card p-4 sm:p-5">
      <form class="hall-filters flex min-w-0 flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-end" @submit.prevent="handleSearch">
        <div class="min-w-0 w-full lg:w-44">
          <label class="label" for="hall-status">状态</label>
          <select id="hall-status" v-model="selectedStatus" class="input h-11">
            <option v-for="option in ORDER_STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </div>

        <div class="flex min-w-0 flex-wrap items-center gap-3 lg:min-h-11">
          <label class="filter-check"><input v-model="openOnly" type="checkbox" /> 仅看可抢</label>
          <label class="filter-check"><input v-model="showHistory" type="checkbox" /> 历史订单</label>
        </div>
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <button type="submit" class="btn-secondary !px-4">筛选</button>
          <button type="button" class="btn-ghost !px-4" @click="resetFilters">重置</button>
        </div>
      </form>
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

        <!-- 抢单倒计时：明确区分当前剩余等待与配置的总等待时长 -->
        <div v-if="getAcceptWaitMetaFor(order).remaining > 0" class="mt-2">
          <span class="tag !bg-warning-soft !text-warning tabular-nums">{{ getAcceptWaitLabel(order) }}</span>
        </div>

        <div v-if="getAttachment(order)" class="mt-3 overflow-hidden rounded-tile"><img :src="getAttachment(order)" alt="订单附件" loading="lazy" class="max-h-40 w-full rounded object-cover" /></div>

        <!-- 层级 3：底部次要信息行（需求摘要 + 游戏名 + 时间）与「查看详情 →」入口 -->
        <div class="mt-4 border-t border-line-1 pt-3.5 text-[13px]">
          <p v-if="buildSummary(order)" class="truncate text-ink-2">{{ buildSummary(order) }}</p>
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

    <!-- 今日已接单（原分页位置）：收起时只留标题栏，查看后最多展开 100 条 -->
    <section v-if="hallRecentClaimsVisible" class="surface-card p-4 sm:p-5" aria-label="今日已接单">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-base font-semibold text-ink-1">
          今日已接单
          <span class="tabular-nums text-ink-2">({{ formatCount(recentClaimsTotal) }})</span>
        </h2>
        <button
          type="button"
          class="btn-ghost !min-h-[36px] shrink-0 !px-4 !py-1.5"
          :disabled="!recentClaimsExpanded && !recentClaimsCanExpand"
          :aria-expanded="recentClaimsExpanded"
          @click="toggleRecentClaims"
        >
          {{ recentClaimsExpanded ? '收起' : '查看' }}
        </button>
      </div>

      <div v-if="recentClaimsExpanded && recentClaimsLoading && !recentClaims.length" class="mt-4 space-y-3" aria-busy="true">
        <div v-for="n in 3" :key="`recent-claim-skeleton-${n}`" class="skeleton-line h-10 w-full"></div>
      </div>
      <div v-else-if="recentClaimsExpanded && visibleRecentClaims.length" class="mt-4">
        <article
          v-for="claim in visibleRecentClaims"
          :key="claim.id"
          class="cursor-pointer border-t border-line-1 py-3 first:border-t-0 first:pt-0"
          @click="goToOrder(claim.order.id)"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <p class="truncate text-[15px] font-semibold text-ink-1">{{ claim.order.title || `订单 #${claim.order.id}` }}</p>
              <p class="mt-1 truncate text-[13px] text-ink-2">{{ getRecentClaimMeta(claim) }}</p>
            </div>
            <div class="shrink-0 text-right">
              <p class="text-[15px] font-semibold tabular-nums text-price">{{ formatPrice(claim.order.price) }}</p>
              <p class="mt-1 text-xs tabular-nums text-ink-3" :title="`打手 ${claim.booster.username} 的保证金余额`">保证金 {{ formatPrice(claim.deposit_balance) }}</p>
            </div>
          </div>
        </article>
      </div>
      <p v-else-if="recentClaimsExpanded && !recentClaimsLoading" class="mt-4 text-[13px] text-ink-3">今天暂无接单记录</p>
    </section>
  </div>
</template>
