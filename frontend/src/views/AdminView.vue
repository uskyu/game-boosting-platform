<script setup>
import { computed, onMounted, ref } from 'vue'

import AdminDashboard from '@/components/admin/AdminDashboard.vue'
import { useGamesStore } from '@/stores/games'
import {
  useWalletStore,
  WITHDRAWAL_STATUS_OPTIONS,
  getChannelLabel,
  getWithdrawalStatusLabel,
  getWithdrawalStatusTagClass,
} from '@/stores/wallet'
import api from '@/utils/api'
import { formatDateTime, formatPrice } from '@/utils/display'
import {
  getApplicationStatusMeta,
  getOrderStatusBadgeClass,
  getOrderStatusLabel,
  getUserRoleLabel,
} from '@/utils/order'
import { getGameCategoryMeta, getGamePlatformLabel } from '@/utils/gameCatalog'

const gamesStore = useGamesStore()
const walletStore = useWalletStore()

const activeTab = ref('dashboard')
const applicationStatus = ref('PENDING')
const applications = ref([])
const orders = ref([])
const loadingApplications = ref(false)
const loadingOrders = ref(false)
const loadingGames = computed(() => gamesStore.loading)
const loadingWithdrawals = computed(() => walletStore.adminWithdrawalsLoading)
const message = ref({ type: '', text: '' })
const submittingKey = ref('')

const reviewForm = ref({})
const orderAction = ref({})

const withdrawalStatus = ref('PENDING')
const adjustForm = ref({ user_id: '', amount: '', reason: '' })
const adjustMessage = ref({ type: '', text: '' })
// 弹窗：assign（派单）/ reject（驳回提现）/ mark-paid（标记已打款）
const modal = ref(null)

const applicationStatusOptions = [
  { value: 'PENDING', label: '待审核' },
  { value: 'APPROVED', label: '已通过' },
  { value: 'REJECTED', label: '已拒绝' },
  { value: '', label: '全部状态' },
]

const dashboardStats = computed(() => [
  { label: '申请', value: applications.value.length },
  { label: '订单', value: orders.value.length },
  { label: '游戏', value: gamesStore.catalogGames.length },
])

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

function applicationMeta(status) {
  return getApplicationStatusMeta(status)
}

async function fetchApplications() {
  loadingApplications.value = true
  try {
    const res = await api.get('/admin/users/applications', {
      params: { status: applicationStatus.value || undefined },
    })
    applications.value = res.data
  } catch (error) {
    message.value = { type: 'error', text: error.message || '加载失败' }
  } finally {
    loadingApplications.value = false
  }
}

async function fetchOrders() {
  loadingOrders.value = true
  try {
    const res = await api.get('/admin/orders', { params: { page: 1, page_size: 50 } })
    orders.value = res.data.items
  } catch (error) {
    message.value = { type: 'error', text: error.message || '加载失败' }
  } finally {
    loadingOrders.value = false
  }
}

async function fetchGames() {
  await gamesStore.fetchGames('', '', { pageSize: 100 })
}

function initReview(userId) {
  if (!reviewForm.value[userId]) {
    reviewForm.value[userId] = { approve: true, booster_quota: 1, review_note: '' }
  }
}

function reviewState(userId) {
  initReview(userId)
  return reviewForm.value[userId]
}

async function submitReview(userId) {
  submittingKey.value = `review-${userId}`
  try {
    await api.put(`/admin/users/${userId}/review`, reviewState(userId))
    message.value = { type: 'success', text: '审核已提交' }
    await fetchApplications()
  } catch (error) {
    message.value = { type: 'error', text: error.message || '提交失败' }
  } finally {
    submittingKey.value = ''
  }
}

function paymentLabel(status) {
  if (status === 'PAID') return '已支付'
  if (status === 'REFUNDED') return '已退款'
  return '待支付'
}

function paymentBadgeClass(status) {
  return {
    tag: true,
    '!bg-amber-400/15 !text-amber-200 !border-amber-400/30': status === 'UNPAID',
    '!bg-emerald-400/15 !text-emerald-200 !border-emerald-400/30': status === 'PAID',
    '!bg-slate-400/10 !text-slate-300 !border-slate-400/20': status === 'REFUNDED',
  }
}

async function handleRefund(orderId) {
  submittingKey.value = `refund-${orderId}`
  try {
    await api.put(`/orders/${orderId}/refund`)
    message.value = { type: 'success', text: '退款成功' }
    await fetchOrders()
  } catch (error) {
    message.value = { type: 'error', text: error.message || '退款失败' }
  } finally {
    submittingKey.value = ''
  }
}

function initOrderAction(orderId) {
  if (!orderAction.value[orderId]) {
    orderAction.value[orderId] = { action: 'DISPUTED', reason: '' }
  }
}

function actionState(orderId) {
  initOrderAction(orderId)
  return orderAction.value[orderId]
}

async function interveneOrder(orderId) {
  submittingKey.value = `order-${orderId}`
  try {
    await api.put(`/admin/orders/${orderId}/intervene`, actionState(orderId))
    message.value = { type: 'success', text: '订单已处理' }
    await fetchOrders()
  } catch (error) {
    message.value = { type: 'error', text: error.message || '处理失败' }
  } finally {
    submittingKey.value = ''
  }
}

// ── 提现处理 ──

async function fetchWithdrawals(page = 1) {
  await walletStore.fetchAdminWithdrawals({
    status: withdrawalStatus.value,
    page,
  })
}

function withdrawalApplicant(item) {
  return (
    item.username ||
    item.user?.username ||
    (item.user_id != null ? `用户 #${item.user_id}` : '未知用户')
  )
}

async function approveWithdrawal(item) {
  submittingKey.value = `withdrawal-${item.id}`
  const result = await walletStore.reviewWithdrawal(item.id, 'approve')
  if (result.success) {
    message.value = { type: 'success', text: '提现申请已通过，等待打款' }
    await fetchWithdrawals(walletStore.adminWithdrawalsPagination.page)
  } else {
    message.value = { type: 'error', text: result.error || '操作失败' }
  }
  submittingKey.value = ''
}

// ── 弹窗（派单 / 驳回提现 / 标记已打款）──

function openAssignModal(order) {
  modal.value = {
    type: 'assign',
    orderId: order.id,
    gameName: order.game_name,
    booster_id: '',
    reason: '',
    error: '',
    submitting: false,
  }
}

function openRejectModal(item) {
  modal.value = {
    type: 'reject',
    withdrawalId: item.id,
    amount: item.amount,
    reason: '',
    error: '',
    submitting: false,
  }
}

function openMarkPaidModal(item) {
  modal.value = {
    type: 'mark-paid',
    withdrawalId: item.id,
    amount: item.amount,
    payment_reference: '',
    error: '',
    submitting: false,
  }
}

function closeModal() {
  modal.value = null
}

function modalTitle() {
  if (modal.value?.type === 'assign') return '订单派单'
  if (modal.value?.type === 'reject') return '驳回提现'
  if (modal.value?.type === 'mark-paid') return '标记已打款'
  return ''
}

async function submitModal() {
  if (!modal.value) return
  const state = modal.value
  state.error = ''

  if (state.type === 'assign') {
    const boosterId = Number(state.booster_id)
    if (!state.booster_id || !Number.isInteger(boosterId) || boosterId <= 0) {
      state.error = '请填写有效的打手用户 ID'
      return
    }
  }

  if (state.type === 'reject' && !state.reason.trim()) {
    state.error = '请填写驳回原因'
    return
  }

  if (state.type === 'mark-paid' && !state.payment_reference.trim()) {
    state.error = '请填写打款流水号'
    return
  }

  state.submitting = true

  if (state.type === 'assign') {
    const result = await walletStore.assignOrder(state.orderId, Number(state.booster_id), state.reason.trim())
    if (!result.success) {
      state.error = result.error || '派单失败'
      state.submitting = false
      return
    }
    message.value = { type: 'success', text: `订单 #${state.orderId} 已派单` }
    await fetchOrders()
  } else if (state.type === 'reject') {
    const result = await walletStore.reviewWithdrawal(state.withdrawalId, 'reject', state.reason.trim())
    if (!result.success) {
      state.error = result.error || '操作失败'
      state.submitting = false
      return
    }
    message.value = { type: 'success', text: '提现申请已驳回' }
    await fetchWithdrawals(walletStore.adminWithdrawalsPagination.page)
  } else if (state.type === 'mark-paid') {
    const result = await walletStore.markPaid(state.withdrawalId, state.payment_reference.trim())
    if (!result.success) {
      state.error = result.error || '操作失败'
      state.submitting = false
      return
    }
    message.value = { type: 'success', text: '已标记已打款' }
    await fetchWithdrawals(walletStore.adminWithdrawalsPagination.page)
  }

  closeModal()
}

// ── 钱包调账 ──

async function submitAdjust() {
  adjustMessage.value = { type: '', text: '' }

  const userId = Number(adjustForm.value.user_id)
  const amount = Number(adjustForm.value.amount)

  if (!adjustForm.value.user_id || !Number.isInteger(userId) || userId <= 0) {
    adjustMessage.value = { type: 'error', text: '请输入有效的用户 ID' }
    return
  }
  if (adjustForm.value.amount === '' || !Number.isFinite(amount) || amount === 0) {
    adjustMessage.value = { type: 'error', text: '金额不能为 0（正数充值、负数扣减）' }
    return
  }
  if (!adjustForm.value.reason.trim()) {
    adjustMessage.value = { type: 'error', text: '请填写调账原因' }
    return
  }

  submittingKey.value = 'wallet-adjust'
  const result = await walletStore.adjustWallet(userId, amount, adjustForm.value.reason.trim())
  if (result.success) {
    adjustMessage.value = {
      type: 'success',
      text: `用户 #${userId} 调账成功（${amount > 0 ? '+' : ''}${formatPrice(amount)}）`,
    }
    adjustForm.value = { user_id: '', amount: '', reason: '' }
  } else {
    adjustMessage.value = { type: 'error', text: result.error || '调账失败' }
  }
  submittingKey.value = ''
}

async function toggleGameStatus(game) {
  submittingKey.value = `game-${game.id}`
  const result = await gamesStore.updateGame(game.id, { is_active: !game.is_active })
  if (result.success) {
    message.value = { type: 'success', text: game.is_active ? '已下架' : '已上架' }
  } else {
    message.value = { type: 'error', text: result.error }
  }
  submittingKey.value = ''
}

async function refreshDashboard() {
  await Promise.all([fetchApplications(), fetchOrders(), fetchGames(), fetchWithdrawals(1)])
}

onMounted(async () => {
  await refreshDashboard()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel scanline-overlay p-6 sm:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div class="space-y-4">
          <p class="eyebrow">管理后台</p>
          <h1 class="section-title neon-text !text-4xl sm:!text-5xl">运营管理台</h1>
        </div>

        <div class="grid gap-4 sm:grid-cols-3">
          <article v-for="item in dashboardStats" :key="item.label" class="stat-card">
            <p class="text-sm text-slate-400">{{ item.label }}</p>
            <p class="mt-2 text-3xl font-semibold text-white">{{ item.value }}</p>
          </article>
        </div>
      </div>
    </section>

    <div v-if="message.text" :class="messageClass(message.type)">{{ message.text }}</div>

    <section class="surface-card p-4 sm:p-5">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center">
        <nav class="tab-bar flex-1" aria-label="管理台模块">
          <button type="button" :class="activeTab === 'dashboard' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'dashboard'">数据看板</button>
          <button type="button" :class="activeTab === 'applications' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'applications'">代练审核</button>
          <button type="button" :class="activeTab === 'orders' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'orders'">订单管理</button>
          <button type="button" :class="activeTab === 'withdrawals' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'withdrawals'">提现处理</button>
          <button type="button" :class="activeTab === 'wallet-adjust' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'wallet-adjust'">钱包调账</button>
          <button type="button" :class="activeTab === 'games' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'games'">游戏管理</button>
        </nav>
        <button class="btn-secondary shrink-0 !px-4 !py-2" @click="refreshDashboard">刷新</button>
      </div>
    </section>

    <AdminDashboard v-if="activeTab === 'dashboard'" />

    <section v-else-if="activeTab === 'applications'" class="surface-card p-6 sm:p-8">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <h2 class="text-2xl font-semibold text-white">代练审核</h2>
        <div class="flex gap-3">
          <select v-model="applicationStatus" class="input min-w-[160px]" @change="fetchApplications">
            <option v-for="option in applicationStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </div>
      </div>

      <div v-if="loadingApplications" class="mt-6 grid gap-4 xl:grid-cols-2" aria-busy="true">
        <div v-for="n in 4" :key="`app-skeleton-${n}`" class="skeleton h-44 !rounded-card"></div>
      </div>

      <div v-else-if="!applications.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">📝</div>
        <h3 class="empty-state__title">暂无申请</h3>
        <p class="empty-state__copy">切换状态筛选，看看其他审核阶段的申请。</p>
      </div>

      <div v-else class="mt-6 grid gap-4 xl:grid-cols-2">
        <article v-for="item in applications" :key="item.user_id" class="catalog-card cyber-corner">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-xl font-semibold text-white">{{ item.username }}</h3>
                <span :class="applicationMeta(item.status).badgeClass">{{ applicationMeta(item.status).label }}</span>
              </div>
              <p class="mt-2 text-sm text-slate-400">{{ item.email }}</p>
              <p class="mt-2 text-sm text-slate-300">{{ item.game_name || '未填游戏' }} · {{ item.current_rank || '-' }} → {{ item.target_rank || '-' }}</p>
            </div>
            <a v-if="item.proof_url" :href="item.proof_url" target="_blank" rel="noreferrer" class="btn-secondary !px-4 !py-2">截图</a>
          </div>

          <div class="mt-5 grid gap-3 sm:grid-cols-3">
            <select class="input" v-model="reviewState(item.user_id).approve">
              <option :value="true">通过</option>
              <option :value="false">拒绝</option>
            </select>
            <input v-model.number="reviewState(item.user_id).booster_quota" type="number" min="0" max="50" class="input" placeholder="名额" />
            <input v-model="reviewState(item.user_id).review_note" class="input" placeholder="备注" />
          </div>

          <div class="mt-5 flex items-center justify-between">
            <p class="text-xs text-slate-500">{{ getUserRoleLabel(item.role) }}</p>
            <button class="btn-primary !px-4 !py-2" :disabled="submittingKey === `review-${item.user_id}`" @click="submitReview(item.user_id)">
              {{ submittingKey === `review-${item.user_id}` ? '提交中...' : '提交' }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <section v-else-if="activeTab === 'orders'" class="surface-card p-6 sm:p-8">
      <h2 class="text-2xl font-semibold text-white">订单管理</h2>

      <div v-if="loadingOrders" class="mt-6 grid gap-4 xl:grid-cols-2" aria-busy="true">
        <div v-for="n in 4" :key="`order-skeleton-${n}`" class="skeleton h-44 !rounded-card"></div>
      </div>

      <div v-else-if="!orders.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">📦</div>
        <h3 class="empty-state__title">暂无订单</h3>
        <p class="empty-state__copy">有新订单进来后，会出现在这里等待处理。</p>
      </div>

      <div v-else class="mt-6 grid gap-4 xl:grid-cols-2">
        <article v-for="order in orders" :key="order.id" class="catalog-card cyber-corner">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-xl font-semibold text-white">#{{ order.id }} {{ order.game_name }}</h3>
                <span :class="getOrderStatusBadgeClass(order.status)">{{ getOrderStatusLabel(order.status) }}</span>
                <span v-if="order.payment_status" :class="paymentBadgeClass(order.payment_status)">{{ paymentLabel(order.payment_status) }}</span>
              </div>
              <p class="mt-2 text-sm text-slate-300">{{ order.current_rank }} → {{ order.target_rank }}</p>
            </div>
            <p class="text-lg font-semibold text-accent-300">{{ formatPrice(order.price) }}</p>
          </div>

          <div class="mt-5 grid gap-3 sm:grid-cols-3">
            <select class="input" v-model="actionState(order.id).action">
              <option value="DISPUTED">争议</option>
              <option value="CANCELLED">取消</option>
              <option value="DELIVERED">标记交付</option>
              <option value="COMPLETED">完结（解决争议）</option>
            </select>
            <input v-model="actionState(order.id).reason" class="input sm:col-span-2" placeholder="原因" />
          </div>

          <div class="mt-5 flex items-center justify-between">
            <p class="text-xs text-slate-500">{{ formatDateTime(order.created_at) }}</p>
            <div class="flex gap-2">
              <button
                v-if="order.status === 'PENDING'"
                class="btn-primary !px-4 !py-2"
                @click="openAssignModal(order)"
              >
                派单
              </button>
              <button
                v-if="order.payment_status === 'PAID' && ['CANCELLED', 'DISPUTED'].includes(order.status)"
                class="btn-secondary !px-4 !py-2"
                :disabled="submittingKey === `refund-${order.id}`"
                @click="handleRefund(order.id)"
              >
                {{ submittingKey === `refund-${order.id}` ? '退款中...' : '退款' }}
              </button>
              <button class="btn-danger !px-4 !py-2" :disabled="submittingKey === `order-${order.id}`" @click="interveneOrder(order.id)">
                {{ submittingKey === `order-${order.id}` ? '处理中...' : '执行' }}
              </button>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section v-else-if="activeTab === 'withdrawals'" class="surface-card p-6 sm:p-8">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <h2 class="text-2xl font-semibold text-white">提现处理</h2>
        <div class="flex gap-3">
          <select v-model="withdrawalStatus" class="input min-w-[160px]" @change="fetchWithdrawals(1)">
            <option v-for="option in WITHDRAWAL_STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </div>
      </div>

      <div v-if="loadingWithdrawals" class="mt-6 space-y-3" aria-busy="true">
        <div v-for="n in 4" :key="`wd-skeleton-${n}`" class="skeleton h-36 !rounded-card"></div>
      </div>

      <div v-else-if="!walletStore.adminWithdrawals.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">💰</div>
        <h3 class="empty-state__title">暂无提现申请</h3>
        <p class="empty-state__copy">用户提交提现后，会按状态出现在这里。</p>
      </div>

      <div v-else class="mt-6 space-y-3">
        <article v-for="item in walletStore.adminWithdrawals" :key="item.id" class="catalog-card cyber-corner">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="space-y-2">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-xl font-semibold text-white">#{{ item.id }} {{ withdrawalApplicant(item) }}</h3>
                <span :class="['tag', getWithdrawalStatusTagClass(item.status)]">{{ getWithdrawalStatusLabel(item.status) }}</span>
              </div>
              <p class="text-sm text-slate-300">
                {{ getChannelLabel(item.channel) }} · {{ item.account_name || '-' }} · {{ item.account_no || '-' }}
              </p>
              <p class="text-xs text-slate-500">申请于 {{ formatDateTime(item.created_at) }}</p>
              <p v-if="item.paid_at" class="text-xs text-slate-500">打款于 {{ formatDateTime(item.paid_at) }}</p>
            </div>

            <p class="text-2xl font-semibold text-accent-300">{{ formatPrice(item.amount) }}</p>
          </div>

          <div
            v-if="item.status === 'REJECTED' && item.reject_reason"
            class="mt-4 rounded-tile border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger-text"
          >
            驳回原因：{{ item.reject_reason }}
          </div>

          <div
            v-if="item.payment_reference"
            class="mt-4 rounded-tile border border-line-soft bg-white/[0.04] px-4 py-3 text-sm text-slate-300"
          >
            打款流水号：{{ item.payment_reference }}
          </div>

          <div class="mt-5 flex items-center justify-end gap-2">
            <button
              v-if="item.status === 'PENDING'"
              class="btn-primary !px-4 !py-2"
              :disabled="submittingKey === `withdrawal-${item.id}`"
              @click="approveWithdrawal(item)"
            >
              {{ submittingKey === `withdrawal-${item.id}` ? '提交中...' : '通过' }}
            </button>
            <button
              v-if="item.status === 'PENDING'"
              class="btn-secondary !px-4 !py-2"
              @click="openRejectModal(item)"
            >
              驳回
            </button>
            <button
              v-if="item.status === 'APPROVED'"
              class="btn-primary !px-4 !py-2"
              @click="openMarkPaidModal(item)"
            >
              标记已打款
            </button>
          </div>
        </article>
      </div>

      <div v-if="walletStore.adminWithdrawalsPagination.pages > 1" class="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm text-slate-400">
          {{ walletStore.adminWithdrawalsPagination.page }} / {{ walletStore.adminWithdrawalsPagination.pages }}
        </p>
        <div class="flex items-center gap-2">
          <button
            class="btn-secondary !px-4 !py-2"
            :disabled="walletStore.adminWithdrawalsPagination.page <= 1"
            @click="fetchWithdrawals(walletStore.adminWithdrawalsPagination.page - 1)"
          >
            上一页
          </button>
          <button
            class="btn-secondary !px-4 !py-2"
            :disabled="walletStore.adminWithdrawalsPagination.page >= walletStore.adminWithdrawalsPagination.pages"
            @click="fetchWithdrawals(walletStore.adminWithdrawalsPagination.page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </section>

    <section v-else-if="activeTab === 'wallet-adjust'" class="surface-card p-6 sm:p-8">
      <h2 class="text-2xl font-semibold text-white">钱包调账</h2>
      <p class="mt-2 text-sm text-slate-400">为指定用户直接调整钱包余额：金额为正表示充值，为负表示扣减。</p>

      <div v-if="adjustMessage.text" class="mt-4" :class="messageClass(adjustMessage.type)">{{ adjustMessage.text }}</div>

      <form class="mt-6 grid gap-5 lg:grid-cols-3" @submit.prevent="submitAdjust">
        <div>
          <label class="label" for="adjust-user-id">用户 ID</label>
          <input id="adjust-user-id" v-model="adjustForm.user_id" type="number" min="1" class="input" placeholder="例如 3" />
        </div>
        <div>
          <label class="label" for="adjust-amount">金额（正充值 / 负扣减）</label>
          <input id="adjust-amount" v-model="adjustForm.amount" type="number" step="0.01" class="input" placeholder="例如 100 或 -50" />
        </div>
        <div>
          <label class="label" for="adjust-reason">原因</label>
          <input id="adjust-reason" v-model="adjustForm.reason" type="text" class="input" placeholder="请填写调账原因" />
        </div>
        <div class="lg:col-span-3">
          <button class="btn-primary w-full py-3 sm:w-auto sm:!px-10" :disabled="submittingKey === 'wallet-adjust'">
            {{ submittingKey === 'wallet-adjust' ? '提交中...' : '提交调账' }}
          </button>
        </div>
      </form>
    </section>

    <section v-else class="surface-card p-6 sm:p-8">
      <h2 class="text-2xl font-semibold text-white">游戏管理</h2>

      <div v-if="loadingGames" class="mt-6 grid gap-4 xl:grid-cols-2" aria-busy="true">
        <div v-for="n in 6" :key="`game-skeleton-${n}`" class="skeleton h-28 !rounded-card"></div>
      </div>

      <div v-else class="mt-6 grid gap-4 xl:grid-cols-2">
        <article v-for="game in gamesStore.catalogGames" :key="game.id" class="catalog-card cyber-corner">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-xl font-semibold text-white">{{ game.name }}</h3>
                <span :class="game.is_active ? 'badge-approved' : 'badge-cancelled'">{{ game.is_active ? '上架' : '下架' }}</span>
              </div>
              <p class="mt-2 text-sm text-slate-300">{{ getGameCategoryMeta(game.category).label }} · {{ getGamePlatformLabel(game.platform) }}</p>
            </div>
            <button class="btn-secondary !px-4 !py-2" :disabled="submittingKey === `game-${game.id}`" @click="toggleGameStatus(game)">
              {{ submittingKey === `game-${game.id}` ? '处理中...' : (game.is_active ? '下架' : '上架') }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <div v-if="modal" class="modal-scrim">
      <div class="absolute inset-0" aria-hidden="true" @click="closeModal"></div>

      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-white">{{ modalTitle() }}</h3>
          <p class="mt-2 text-sm text-slate-400">
            {{
              modal.type === 'assign'
                ? `订单 #${modal.orderId} ${modal.gameName || ''}`
                : `提现 #${modal.withdrawalId} · ${formatPrice(modal.amount)}`
            }}
          </p>

          <div v-if="modal.error" class="message-error mt-4">{{ modal.error }}</div>

          <form class="mt-5 space-y-4" @submit.prevent="submitModal">
            <div v-if="modal.type === 'assign'">
              <label class="label" for="assign-booster-id">打手用户 ID</label>
              <input id="assign-booster-id" v-model="modal.booster_id" type="number" min="1" class="input" placeholder="例如 5" />
            </div>

            <div v-if="modal.type === 'assign'">
              <label class="label" for="assign-reason">派单原因（可选）</label>
              <input id="assign-reason" v-model="modal.reason" type="text" class="input" placeholder="选填" />
            </div>

            <div v-if="modal.type === 'reject'">
              <label class="label" for="reject-reason">驳回原因</label>
              <textarea id="reject-reason" v-model="modal.reason" rows="3" class="input resize-none" placeholder="请填写驳回原因"></textarea>
            </div>

            <div v-if="modal.type === 'mark-paid'">
              <label class="label" for="mark-paid-reference">打款流水号</label>
              <input id="mark-paid-reference" v-model="modal.payment_reference" type="text" class="input" placeholder="支付宝 / 微信 / 银行转账单号" />
            </div>

            <div class="flex justify-end gap-3 pt-2">
              <button type="button" class="btn-ghost !px-4 !py-2" @click="closeModal">取消</button>
              <button type="submit" class="btn-primary !px-5 !py-2" :disabled="modal.submitting">
                {{ modal.submitting ? '提交中...' : '确认' }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>
