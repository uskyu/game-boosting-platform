<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useOrdersStore } from '@/stores/orders'
import { formatCount, formatPrice, formatShortDate } from '@/utils/display'
import { ORDER_STATUS_OPTIONS, getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'
import { getGameImage } from '@/data/gameImages'

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

const headerStats = computed(() => [
  { value: formatCount(orders.value.length), label: '当前页' },
  { value: formatCount(orders.value.filter((item) => item.status === 'PENDING').length), label: '待接单' },
  { value: formatCount(orders.value.filter((item) => item.status === 'LOCKED').length), label: '进行中' },
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

onMounted(async () => {
  fetchOrders()
  await chatStore.fetchConversations({ pageSize: 100 })
  await chatStore.fetchUnreadSummary()
})

onUnmounted(() => {
  window.clearTimeout(searchTimeout)
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <div class="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div class="space-y-3">
          <p class="eyebrow">我的订单</p>
          <h1 class="section-title">订单列表</h1>
          <p class="section-copy max-w-xl">全部需求的列表视图，可按游戏与状态筛选。</p>
        </div>

        <router-link
          v-if="!isBooster && !isAdmin"
          to="/orders/create"
          class="btn-primary shrink-0 self-start px-6"
        >
          发布需求
        </router-link>
      </div>

      <div class="mt-6 grid grid-cols-3 gap-3 sm:gap-4">
        <article
          v-for="item in headerStats"
          :key="item.label"
          class="stat-card"
        >
          <p class="stat-value text-ink-1">{{ item.value }}</p>
          <p class="mt-1.5 text-[13px] text-ink-2">{{ item.label }}</p>
        </article>
      </div>
    </section>

    <section class="surface-card p-5 sm:p-6">
      <div class="grid gap-4 lg:grid-cols-[1.2fr_240px_auto] lg:items-end">
        <div>
          <label class="label" for="order-search">游戏</label>
          <input id="order-search" v-model="searchGame" type="text" class="input" placeholder="搜索游戏" />
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

    <div v-if="error" class="message-error">{{ error }}</div>

    <section v-if="loading" class="grid gap-4 xl:grid-cols-2" aria-busy="true">
      <div v-for="n in 4" :key="`skeleton-${n}`" class="skeleton-row">
        <div class="skeleton h-14 w-14 !rounded-tile"></div>
        <div class="flex-1 space-y-3">
          <div class="skeleton-line h-4 w-2/5"></div>
          <div class="skeleton-line h-3 w-3/5"></div>
        </div>
        <div class="space-y-3">
          <div class="skeleton-line ml-auto h-6 w-20"></div>
          <div class="skeleton-line ml-auto h-3 w-16"></div>
        </div>
      </div>
    </section>

    <section v-else-if="orders.length" class="grid gap-4 xl:grid-cols-2">
      <article
        v-for="order in orders"
        :key="order.id"
        class="catalog-card cyber-corner cursor-pointer"
        @click="goToOrder(order.id)"
      >
        <div class="flex items-start justify-between gap-4">
          <div class="flex items-start gap-4">
            <div
              class="flex h-14 w-14 items-center justify-center rounded-tile border text-lg font-semibold text-ink-1"
              :style="gameBadgeStyle(order.game_name)"
            >
              {{ order.game_name?.slice(0, 1) || '?' }}
            </div>

              <div class="space-y-3">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="text-xl font-semibold text-ink-1">{{ order.game_name }}</h2>
                <span v-if="getOrderUnreadCount(order.id)" class="tag !bg-warning-soft !text-warning">
                  消息 {{ getOrderUnreadCount(order.id) }}
                </span>
              </div>
              <p class="text-sm text-ink-2">{{ buildSummary(order) }}</p>
            </div>
          </div>

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

        <div class="mt-5 grid gap-3 sm:grid-cols-3">
          <div class="info-tile">
            <p class="info-tile__label">目标</p>
            <p class="info-tile__value">{{ order.current_rank }} → {{ order.target_rank }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">价格</p>
            <p class="info-tile__value text-base font-semibold tabular-nums text-price">{{ formatPrice(order.price) }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">时间</p>
            <p class="info-tile__value">{{ formatShortDate(order.created_at) }}</p>
          </div>
        </div>

        <div class="mt-5 flex items-center justify-between gap-4 border-t border-line-1 pt-4">
          <p class="text-sm text-ink-3">#{{ order.id }}</p>
          <div class="flex gap-2">
            <button
              v-if="isBooster && order.status === 'PENDING' && order.user_id !== currentUserId"
              class="btn-primary !px-4 !py-2"
              @click="handleAcceptOrder(order.id, $event)"
            >
              接单
            </button>
            <button class="btn-secondary !px-4 !py-2" @click.stop="goToOrder(order.id)">详情</button>
          </div>
        </div>
      </article>
    </section>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🗂️</div>
      <h2 class="empty-state__title">暂无订单</h2>
      <p class="empty-state__copy">换个筛选条件试试，或者稍后回来看看新需求。</p>
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
  </div>
</template>
