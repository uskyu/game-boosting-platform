<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { useOrdersStore } from '@/stores/orders'
import { formatCount, formatDateTime, formatOrderPrice, formatPayoutDelay, formatPrice, formatShortDate } from '@/utils/display'
import { ORDER_STATUS_OPTIONS, getClaimSettlementMeta, getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'

const route = useRoute()
const router = useRouter()
const chatStore = useChatStore()
const ordersStore = useOrdersStore()

// 双栏：我的接单（claims/mine）/ 我的派单（自己发布的订单）
// 搜索与 Tab 状态同步进 URL query：从详情页/其他页面返回、刷新、前进后退都能恢复，
// 否则组件重建即清空搜索，列表闪回全量（老板反馈的"搜索失效回归大厅"）。
const activeTab = ref(route.query.tab === 'claims' || route.query.tab === 'published' ? route.query.tab : 'claims')
let userTouchedTab = false
const searchGame = ref(String(route.query.game || ''))
const searchBossContact = ref(String(route.query.boss || ''))
const selectedStatus = ref(String(route.query.status || ''))
const claimStatus = ref(String(route.query.claim_status || ''))

// 管理员默认"我的派单"：整页刷新时用户信息可能晚于挂载到达，用 watch 兜底切换。
// URL 已指定 tab 时以 URL 为准。
watch(() => useAuthStore().isAdmin, (isAdmin) => {
  if (isAdmin && !userTouchedTab && !route.query.tab) {
    activeTab.value = 'published'
    syncQuery()
  }
}, { immediate: true })

const orders = computed(() => ordersStore.orders)
const myClaims = computed(() => ordersStore.myClaims)
const claimsLoading = computed(() => ordersStore.myClaimsLoading)
const claimsPagination = computed(() => ordersStore.myClaimsPagination)
const loading = computed(() => ordersStore.loading)
const pagination = computed(() => ordersStore.pagination)
const error = computed(() => ordersStore.error)
// 我的派单列表由服务端 mine_published=true 保证仅包含当前用户发布的订单
const publishedOrders = computed(() => orders.value)
const pendingReviewTotal = computed(() => publishedOrders.value.reduce((sum, order) => sum + Number(order.pending_review_count || 0), 0))
const pendingSettlementTotal = computed(() => publishedOrders.value.reduce((sum, order) => sum + Number(order.settled_count || 0), 0))

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

const claimStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'CLAIMED', label: '进行中' },
  { value: 'DELIVERED', label: '待审核' },
  { value: 'SETTLED', label: '已结算' },
  { value: 'CANCELLED', label: '已取消' },
  // 后端 GET /orders/claims/mine 的 status 支持 CLAIMED/DELIVERED/SETTLED/CANCELLED，
  // DISPUTED 走本地过滤（按 claim.order.status === 'DISPUTED'），不调服务端过滤
  { value: 'DISPUTED', label: '争议中' },
]

// 争议筛选为本地过滤：claim 自身状态仍是 CLAIMED/DELIVERED，看关联订单是否 DISPUTED
const displayedClaims = computed(() => {
  if (claimStatus.value === 'DISPUTED') {
    return myClaims.value.filter((claim) => claim.order?.status === 'DISPUTED' || claim.status === 'DISPUTED')
  }
  return myClaims.value
})

function getOrderUnreadCount(orderId) {
  return Number(unreadMap.value[orderId] || 0)
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
    bossContact: searchBossContact.value.trim(),
  })
  await ordersStore.fetchOrders({ minePublished: true })
}

async function fetchClaims() {
  // DISPUTED 本地过滤：服务端不支持该值，拉全量再过滤
  const serverStatus = claimStatus.value === 'DISPUTED' ? undefined : (claimStatus.value || undefined)
  await ordersStore.fetchMyClaims(serverStatus)
}

function switchTab(tab) {
  activeTab.value = tab
  userTouchedTab = true
  if (tab === 'published') {
    ordersStore.setPage(1)
    fetchOrders()
  }
  syncQuery(1)
}

// 把当前 Tab/搜索/筛选/页码写进 URL（replace 不产生历史噪音）
function syncQuery(page = ordersStore.pagination.page) {
  router.replace({
    query: {
      tab: activeTab.value,
      game: searchGame.value || undefined,
      boss: searchBossContact.value || undefined,
      status: selectedStatus.value || undefined,
      claim_status: claimStatus.value || undefined,
      page: page > 1 ? page : undefined,
    },
  })
}

// 浏览器前进/后退只改 query 不重建组件：从这里恢复状态并重拉列表。
// 同步写入（syncQuery）产生的 query 变化各字段与 refs 相同，不会触发多余请求。
let restoringFromQuery = false

watch(() => route.query, (query) => {
  const qTab = query.tab === 'claims' ? 'claims' : query.tab === 'published' ? 'published' : null
  const qGame = String(query.game || '')
  const qBoss = String(query.boss || '')
  const qStatus = String(query.status || '')
  const qClaim = String(query.claim_status || '')
  const qPage = Math.max(1, Number(query.page) || 1)

  restoringFromQuery = true
  let changed = false
  if (qTab && activeTab.value !== qTab) { activeTab.value = qTab; changed = true }
  if (searchGame.value !== qGame) { searchGame.value = qGame; changed = true }
  if (searchBossContact.value !== qBoss) { searchBossContact.value = qBoss; changed = true }
  if (selectedStatus.value !== qStatus) { selectedStatus.value = qStatus; changed = true }
  if (claimStatus.value !== qClaim) { claimStatus.value = qClaim; changed = true }
  if (ordersStore.pagination.page !== qPage) { ordersStore.setPage(qPage); changed = true }
  nextTick(() => { restoringFromQuery = false })

  if (changed) fetchOrders()
})

function handleSearch() {
  ordersStore.setPage(1)
  fetchOrders()
  syncQuery(1)
}

function resetFilters() {
  searchGame.value = ''
  searchBossContact.value = ''
  selectedStatus.value = ''
  claimStatus.value = ''
  ordersStore.setPage(1)
  fetchOrders()
  fetchClaims()
  syncQuery(1)
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
  syncQuery(page)
}

let searchTimeout = null

watch(searchGame, () => {
  window.clearTimeout(searchTimeout)
  // URL 恢复触发的变化已由 restore 流程直接拉取，防抖不必再补一枪
  const fromRestore = restoringFromQuery
  searchTimeout = window.setTimeout(() => {
    if (fromRestore) return
    ordersStore.setPage(1)
    fetchOrders()
    syncQuery(1)
  }, 300)
})

watch(claimStatus, () => {
  fetchClaims()
})

onMounted(async () => {
  // 从 URL 恢复页码后再拉首屏，避免返回/刷新时搜索条件在但页码被重置
  ordersStore.setPage(Math.max(1, Number(route.query.page) || 1))
  fetchOrders()
  if (!useAuthStore().isAdmin) fetchClaims()
  // 管理员不拉报名列表；若用户信息晚到，watch 会切到"我的派单"
  // 并行拉取：会话列表与未读数互不依赖
  await Promise.all([
    chatStore.fetchConversations({ pageSize: 100 }),
    chatStore.fetchUnreadSummary(),
  ])
})

onUnmounted(() => {
  window.clearTimeout(searchTimeout)
})
</script>

<template>
  <div class="page-shell space-y-5">
    <!-- 双 Tab：我的接单 / 我的派单 -->
    <section class="surface-card p-4 sm:p-5">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="tab-bar !mb-0">
          <button type="button" :class="activeTab === 'claims' ? 'tab-pill-active' : 'tab-pill'" @click="switchTab('claims')">
            我的接单
            <span v-if="myClaims.length" class="ml-1 tabular-nums">({{ myClaims.length }})</span>
          </button>
          <button type="button" :class="activeTab === 'published' ? 'tab-pill-active' : 'tab-pill'" @click="switchTab('published')">
            我的派单
            <span v-if="pendingReviewTotal" class="ml-1 rounded-full bg-warning/15 px-1.5 text-xs font-bold text-warning tabular-nums">{{ pendingReviewTotal }} 待审核</span>
          </button>
        </div>
        <router-link to="/orders/create" class="btn-primary shrink-0 !px-4 !py-2">发布订单</router-link>
      </div>
    </section>

    <div v-if="error" class="message-error">{{ error }}</div>

    <!-- ── 我的接单：打手自己接的活（进行中 / 待审核 / 已结算） ── -->
    <template v-if="activeTab === 'claims'">
      <section class="surface-card p-4 sm:p-5">
        <div class="flex flex-wrap items-end justify-between gap-3">
          <div>
            <label class="label" for="claim-status">接单状态</label>
            <select id="claim-status" v-model="claimStatus" class="input">
              <option v-for="option in claimStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </div>
          <p class="text-xs text-ink-3">完成后提交汇报，等发布人审核打款</p>
        </div>
      </section>

      <section v-if="claimsLoading" class="space-y-3" aria-busy="true">
        <div v-for="n in 3" :key="`cs-${n}`" class="skeleton-row">
          <div class="flex-1 space-y-3">
            <div class="skeleton-line h-4 w-2/5"></div>
            <div class="skeleton-line h-3 w-3/5"></div>
          </div>
        </div>
      </section>

      <section v-else-if="displayedClaims.length" class="space-y-3">
        <article
          v-for="claim in displayedClaims"
          :key="claim.id"
          class="surface-card cursor-pointer p-4 sm:p-5"
          @click="goToOrder(claim.order?.id || claim.order_id)"
        >
          <h2 class="truncate text-[15px] font-semibold text-ink-1">{{ claim.order?.title || claim.order?.game_name || '代练订单' }}</h2>
          <p v-if="claim.order && getMetaLine(claim.order)" class="mt-1.5 truncate text-[13px] tabular-nums text-ink-2">{{ getMetaLine(claim.order) }}</p>
          <p v-if="claim.order" class="mt-1.5 truncate text-sm text-ink-2">{{ buildSummary(claim.order) }}</p>
          <div v-if="claim.order && getAttachment(claim.order)" class="mt-3 overflow-hidden rounded-tile"><img :src="getAttachment(claim.order)" alt="订单附件" loading="lazy" class="max-h-40 w-full rounded object-cover" /></div>
          <div class="mt-4 border-t border-line-1 pt-3.5">
            <div class="flex flex-wrap items-end justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span :class="getClaimSettlementMeta(claim).tagClass">{{ getClaimSettlementMeta(claim).label }}</span>
                  <span v-if="claim.order?.status === 'DISPUTED'" class="tag !bg-danger-soft !text-danger">订单争议中</span>
                  <span v-if="claim.order?.status === 'CANCELLED' && claim.status !== 'CANCELLED'" class="tag !bg-danger-soft !text-danger">订单已取消</span>
                </div>
                <p class="mt-2 truncate text-[13px] text-ink-3">
                  {{ claim.order?.game_name || '' }} · {{ formatShortDate(claim.created_at) }} · 接单 #{{ claim.id }} · 订单 #{{ claim.order?.id || claim.order_id }}<template v-if="claim.delivered_at"> · 交付于 {{ formatDateTime(claim.delivered_at) }}</template>
                </p>
              </div>
              <div class="flex shrink-0 gap-2">
                <div class="info-tile info-tile--compact">
                  <p class="info-tile__label">价格</p>
                  <p class="info-tile__value text-sm font-semibold tabular-nums text-price">{{ claim.order ? formatOrderPrice(claim.order) : formatPrice(0) }}</p>
                </div>
                <div class="info-tile info-tile--compact">
                  <p class="info-tile__label">接单状态</p>
                  <p class="info-tile__value text-sm">{{ claim.order?.status === 'CANCELLED' ? '已取消' : getClaimSettlementMeta(claim).label }}</p>
                </div>
              </div>
            </div>
          </div>
        </article>
      </section>

      <section v-else class="empty-state">
        <div class="empty-state__icon" aria-hidden="true">🎮</div>
        <h2 class="empty-state__title">还没接过单</h2>
        <p class="empty-state__copy">去大厅看看，有合适的单子直接接。</p>
      </section>

      <section v-if="claimsPagination.pages > 1" class="surface-card p-5">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-sm text-ink-2">{{ claimsPagination.page }} / {{ claimsPagination.pages }} · {{ formatCount(claimsPagination.total) }}</p>
          <div class="flex items-center gap-2">
            <button class="btn-secondary !px-4 !py-2" :disabled="claimsPagination.page <= 1" @click="ordersStore.fetchMyClaims(claimStatus || undefined, claimsPagination.page - 1)">上一页</button>
            <button class="btn-secondary !px-4 !py-2" :disabled="claimsPagination.page >= claimsPagination.pages" @click="ordersStore.fetchMyClaims(claimStatus || undefined, claimsPagination.page + 1)">下一页</button>
          </div>
        </div>
      </section>
    </template>

    <!-- ── 我的派单：自己发布的订单（点进去审核交付、打款） ── -->
    <template v-else>
      <section class="surface-card p-4 sm:p-5">
        <div class="grid gap-4 lg:grid-cols-[1.2fr_1fr_240px_auto] lg:items-end">
          <div>
            <label class="label" for="order-search">游戏</label>
            <input id="order-search" v-model="searchGame" type="text" class="input" placeholder="搜索游戏" />
          </div>
          <div>
            <label class="label" for="order-boss-contact">老板ID</label>
            <input id="order-boss-contact" v-model="searchBossContact" type="text" class="input" maxlength="64" placeholder="搜老板ID" @keyup.enter="handleSearch" />
          </div>
          <div>
            <label class="label" for="order-status">状态</label>
            <select id="order-status" v-model="selectedStatus" class="input" @change="handleSearch">
              <option v-for="option in ORDER_STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </div>
          <div class="flex gap-3 lg:self-end">
            <button class="btn-secondary !px-4 !py-2.5" @click="handleSearch">筛选</button>
            <button class="btn-ghost !px-4 !py-2.5" @click="resetFilters">重置</button>
          </div>
        </div>
      </section>

      <section v-if="loading" class="grid gap-4 xl:grid-cols-2" aria-busy="true">
        <div v-for="n in 4" :key="`skeleton-${n}`" class="skeleton-row">
          <div class="flex-1 space-y-3">
            <div class="skeleton-line h-4 w-2/5"></div>
            <div class="skeleton-line h-3 w-3/5"></div>
          </div>
        </div>
      </section>

      <section v-else-if="publishedOrders.length" class="grid gap-4 xl:grid-cols-2">
        <article
          v-for="order in publishedOrders"
          :key="order.id"
          class="catalog-card cyber-corner cursor-pointer"
          @click="goToOrder(order.id)"
        >
          <h2 class="truncate text-[15px] font-semibold text-ink-1">{{ order.title || order.game_name }}</h2>
          <p v-if="getMetaLine(order)" class="mt-1.5 truncate text-[13px] tabular-nums text-ink-2">{{ getMetaLine(order) }}</p>
          <p class="mt-1.5 truncate text-sm text-ink-2">{{ buildSummary(order) }}</p>
          <div v-if="getAttachment(order)" class="mt-3 overflow-hidden rounded-tile"><img :src="getAttachment(order)" alt="订单附件" loading="lazy" class="max-h-40 w-full rounded object-cover" /></div>

          <div class="mt-4 border-t border-line-1 pt-3.5">
            <div class="flex flex-wrap items-end justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <span :class="getOrderStatusBadgeClass(order.status)">{{ getOrderStatusLabel(order.status) }}</span>
                  <span v-if="Number(order.pending_review_count)" class="tag !bg-warning-soft !text-warning">
                    {{ order.pending_review_count }} 人待审核
                  </span>
                  <span v-if="getOrderUnreadCount(order.id)" class="tag !bg-warning-soft !text-warning">
                    消息 {{ getOrderUnreadCount(order.id) }}
                  </span>
                </div>
                <p class="mt-2 truncate text-[13px] text-ink-3">{{ order.game_name }} · {{ formatShortDate(order.created_at) }} · #{{ order.id }}<template v-if="order.boss_contact"> · 老板ID {{ order.boss_contact }}</template></p>
              </div>
              <div class="flex shrink-0 gap-2">
                <div class="info-tile info-tile--compact">
                  <p class="info-tile__label">价格</p>
                  <p class="info-tile__value text-sm font-semibold tabular-nums text-price">{{ formatOrderPrice(order) }}</p>
                </div>
                <div class="info-tile info-tile--compact">
                  <p class="info-tile__label">接单情况</p>
                  <p class="info-tile__value text-sm tabular-nums">{{ Number(order.claimed_count ?? 0) }} / {{ Number(order.max_claims ?? 1) }} 人</p>
                </div>
              </div>
            </div>
            <div class="mt-3 flex justify-end">
              <button class="btn-secondary !px-4 !py-2" @click.stop="goToOrder(order.id)">
                {{ Number(order.pending_review_count) ? '去审核' : '详情' }}
              </button>
            </div>
          </div>
        </article>
      </section>

      <section v-else class="empty-state">
        <div class="empty-state__icon" aria-hidden="true">🗂️</div>
        <h2 class="empty-state__title">还没有派过单</h2>
        <p class="empty-state__copy">点右上角「发布订单」，把需求挂到大厅让打手接。</p>
      </section>

      <section v-if="pagination.pages > 1" class="surface-card p-5">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-sm text-ink-2">
            {{ pagination.page }} / {{ pagination.pages }} · {{ formatCount(pagination.total) }}
          </p>
          <div class="flex items-center gap-2">
            <button class="btn-secondary !px-4 !py-2" :disabled="pagination.page <= 1" @click="handlePageChange(pagination.page - 1)">上一页</button>
            <button class="btn-secondary !px-4 !py-2" :disabled="pagination.page >= pagination.pages" @click="handlePageChange(pagination.page + 1)">下一页</button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
