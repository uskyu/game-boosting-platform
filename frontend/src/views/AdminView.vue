<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import AdminDashboard from '@/components/admin/AdminDashboard.vue'
import AdminUserList from '@/components/admin/AdminUserList.vue'
import AdminSiteSettings from '@/components/admin/AdminSiteSettings.vue'
import { useOrdersStore } from '@/stores/orders'
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
import {
  GAME_CATEGORY_META,
  GAME_PLATFORM_META,
  getGameCategoryMeta,
  getGamePlatformLabel,
} from '@/utils/gameCatalog'

const route = useRoute()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)
const gamesStore = useGamesStore()
const ordersStore = useOrdersStore()
const walletStore = useWalletStore()

const TAB_KEYS = ['dashboard', 'applications', 'orders', 'withdrawals', 'wallet-adjust', 'games', 'users', 'site']

function normalizeTab(tab) {
  const value = Array.isArray(tab) ? tab[0] : tab
  return TAB_KEYS.includes(value) ? value : 'dashboard'
}

// 支持 /admin?tab=orders 直达指定 tab（大厅管理员「发布订单」入口跳转用）
const activeTab = ref(normalizeTab(route.query.tab))

watch(
  () => route.query.tab,
  (tab) => {
    const next = normalizeTab(tab)
    if (next !== activeTab.value) {
      activeTab.value = next
    }
  }
)

const applicationStatus = ref('PENDING')
const applications = ref([])
const orders = ref([])
const loadingApplications = ref(false)
const loadingOrders = ref(false)
const loadingGames = computed(() => gamesStore.adminLoading)
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
  { label: '游戏', value: gamesStore.adminGames.length },
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
    '!bg-warning-soft !text-warning': status === 'UNPAID',
    '!bg-success-soft !text-success': status === 'PAID',
    '!bg-surface-3 !text-ink-2': status === 'REFUNDED',
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

async function controlOrder(orderId, action) {
  submittingKey.value = `order-control-${orderId}`
  try {
    const response = await api.put(`/orders/${orderId}/claim-control`, { action })
    const index = orders.value.findIndex((order) => order.id === orderId)
    if (index !== -1) orders.value.splice(index, 1, response.data)
    message.value = { type: 'success', text: '订单控制已更新' }
  } catch (error) { message.value = { type: 'error', text: error.message || '订单控制失败' } }
  finally { submittingKey.value = '' }
}

async function deleteOrder(orderId) {
  submittingKey.value = `order-delete-${orderId}`
  try { await api.delete(`/orders/${orderId}`); orders.value = orders.value.filter((order) => order.id !== orderId); message.value = { type: 'success', text: '订单已删除' } }
  catch (error) { message.value = { type: 'error', text: error.message || '删除失败' } }
  finally { submittingKey.value = '' }
}

async function bulkOrderAction(action) {
  if (!selectedOrderIds.value.length) return
  for (const id of selectedOrderIds.value) await controlOrder(id, action)
  selectedOrderIds.value = []
  await fetchOrders()
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

// ── 错误信息格式化（后端 detail 可能是字符串，也可能是 422 校验数组）──

function formatApiError(value) {
  if (Array.isArray(value)) {
    return value
      .map((item, index) => `${index + 1}. ${item?.msg || JSON.stringify(item)}`)
      .join('；')
  }
  return value
}

// ── 发布订单（管理员发单：POST /orders/create → 大厅 PENDING + 广播打手）──

const publishModal = ref(null)
const attachmentTypes = ['image/png', 'image/jpeg', 'image/webp']
const maxAttachmentCount = 5
const maxAttachmentSize = 5 * 1024 * 1024

function validateOrderAttachments(files) {
  const selected = Array.from(files || [])
  if (selected.length > maxAttachmentCount) return '订单最多上传5张图片'
  const invalid = selected.find((file) => !attachmentTypes.includes((file.type || '').toLowerCase()))
  if (invalid) return `仅支持 PNG、JPEG、WebP 图片：${invalid.name}`
  const oversized = selected.find((file) => file.size > maxAttachmentSize)
  if (oversized) return `单张图片不能超过5MB：${oversized.name}`
  return ''
}

async function uploadOrderAttachments(orderId, files, state) {
  const selected = Array.from(files || [])
  for (let index = 0; index < selected.length; index += 1) {
    const file = selected[index]
    state.uploading = `${index + 1}/${selected.length}`
    const body = new FormData()
    body.append('attachment', file)
    try {
      await api.post(`/orders/${orderId}/attachments`, body, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (event) => {
          const percent = event.total ? Math.round((event.loaded / event.total) * 100) : 0
          state.uploadProgress = `${index + 1}/${selected.length}（${percent}%）`
        },
      })
    } catch (error) {
      throw new Error(`第 ${index + 1} 张图片上传失败：${error.message || '请稍后重试'}`)
    }
  }
  state.uploading = ''
  state.uploadProgress = ''
}

const publishPriorityOptions = [
  { value: 1, label: '普通' },
  { value: 5, label: '加急' },
]

const publishGameOptions = computed(() => gamesStore.adminGames)
const hasActivePublishGame = computed(() => publishGameOptions.value.some((game) => game.is_active))
const selectedPublishGame = computed(() => {
  const gameId = Number(publishModal.value?.game_id)
  return publishGameOptions.value.find((game) => game.id === gameId) || null
})

async function openPublishModal() {
  publishModal.value = {
    game_id: '',
    title: '',
    intro: '',
    current_rank: '',
    target_rank: '',
    price: '100',
    price_min: '',
    price_max: '',
    max_claims: 1,
    deadline: '',
    attachments: null,
    description: '',
    priority: 1,
    server: '',
    error: '',
    submitting: false,
    uploading: '',
    uploadProgress: '',
  }

  if (!gamesStore.adminGames.length) {
    await gamesStore.fetchAdminGames({ pageSize: 200 })
  }

  // 默认选中第一个已上架游戏；全部未上架时也可选择未上架游戏发单
  if (publishModal.value && !publishModal.value.game_id) {
    const games = gamesStore.adminGames
    const firstActive = games.find((game) => game.is_active)
    publishModal.value.game_id = (firstActive || games[0])?.id ?? ''
  }
}

function closePublishModal() {
  publishModal.value = null
}

async function submitPublishModal() {
  const state = publishModal.value
  if (!state) return
  state.error = ''

  const attachmentError = validateOrderAttachments(state.attachments)
  if (attachmentError) {
    state.error = attachmentError
    return
  }

  const game = selectedPublishGame.value
  if (!game) {
    state.error = gamesStore.adminGames.length
      ? '请选择游戏'
      : '暂无游戏可选，请先在「游戏管理」中新建游戏'
    return
  }
  if (!state.current_rank.trim()) {
    state.error = '请填写当前段位'
    return
  }
  if (!state.target_rank.trim()) {
    state.error = '请填写目标段位'
    return
  }
  const price = Number(state.price)
  if (!Number.isFinite(price) || price <= 0) {
    state.error = '请填写大于 0 的价格'
    return
  }

  state.submitting = true
  const result = await ordersStore.createOrder({
    game_id: game.id,
    game_name: game.name,
    title: state.title.trim() || `${game.name} 代练订单`,
    intro: state.intro.trim() || state.description.trim() || null,
    current_rank: state.current_rank.trim(),
    target_rank: state.target_rank.trim(),
    price,
    price_min: state.price_min ? Number(state.price_min) : null,
    price_max: state.price_max ? Number(state.price_max) : null,
    max_claims: Number(state.max_claims) || 1,
    deadline: state.deadline || null,
    description_raw: state.description.trim() || null,
    priority: state.priority,
    server: state.server.trim() || null,
  })
  if (!result.success) {
    state.submitting = false
    state.error = formatApiError(result.error) || '发布失败'
    return
  }

  try {
    await uploadOrderAttachments(result.data.id, state.attachments, state)
  } catch (error) {
    state.submitting = false
    state.uploading = ''
    state.error = `订单已创建，但${error.message}`
    await fetchOrders()
    return
  }
  state.submitting = false
  message.value = { type: 'success', text: `订单 #${result.data?.id ?? ''} 已发布到大厅，等待打手接单` }
  closePublishModal()
  await fetchOrders()
}

// ── 游戏管理（GET/POST/PUT/DELETE /admin/games，全量含未上架）──

const gameStatusFilter = ref('')
const gameModal = ref(null)
const gameDeleteModal = ref(null)
const selectedGameIds = ref([])
const logoInputs = ref({})
const selectedOrderIds = ref([])

const gameStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'active', label: '已上架' },
  { value: 'inactive', label: '未上架' },
]

const gameCategoryOptions = GAME_CATEGORY_META.map((meta) => ({ value: meta.value, label: meta.label }))
const gamePlatformOptions = Object.entries(GAME_PLATFORM_META).map(([value, meta]) => ({ value, label: meta.label }))

async function fetchGamesWithFilter(page = 1) {
  await gamesStore.fetchAdminGames({
    page,
    pageSize: 200,
    isActive:
      gameStatusFilter.value === 'active'
        ? true
        : gameStatusFilter.value === 'inactive'
          ? false
          : undefined,
  })
}

function handleGamePageChange(page) {
  const pagination = gamesStore.adminPagination
  if (page < 1 || page > pagination.pages || page === pagination.page) {
    return
  }
  fetchGamesWithFilter(page)
}

// 新建游戏默认模板：按分类推导（后端 GameCreate 必填 service_template）
function buildDefaultServiceTemplate(category) {
  const hasRankSystem = ['MOBA', 'FPS', 'RACING', 'CARD'].includes(category)
  return {
    service_types: ['代练上分', '陪玩', '教学'],
    has_rank_system: hasRankSystem,
    rank_tiers: hasRankSystem ? ['青铜', '白银', '黄金', '铂金', '钻石', '大师', '王者'] : [],
    servers: [],
    roles: [],
    custom_fields: [],
  }
}

function toggleGameSelection(id) {
  selectedGameIds.value = selectedGameIds.value.includes(id) ? selectedGameIds.value.filter((item) => item !== id) : [...selectedGameIds.value, id]
}

function toggleAllGames() {
  selectedGameIds.value = selectedGameIds.value.length === gamesStore.adminGames.length ? [] : gamesStore.adminGames.map((game) => game.id)
}

async function bulkGameAction(action) {
  const result = await gamesStore.bulkAction(action, selectedGameIds.value)
  if (result.success) {
    message.value = { type: 'success', text: action === 'delete' ? '游戏已批量删除' : action === 'activate' ? '游戏已批量上架' : '游戏已批量下架' }
    selectedGameIds.value = []
  } else message.value = { type: 'error', text: formatApiError(result.error) || '批量操作失败' }
}

function handleLogoChange(gameId, event) {
  const file = event.target.files?.[0]
  if (!file) return
  submittingKey.value = `logo-${gameId}`
  gamesStore.uploadLogo(gameId, file).then((result) => {
    message.value = result.success ? { type: 'success', text: 'Logo 已更新' } : { type: 'error', text: result.error || 'Logo 上传失败' }
    submittingKey.value = ''
    event.target.value = ''
  })
}

function openGameCreateModal() {
  gameModal.value = {
    mode: 'create',
    id: null,
    name: '',
    english_name: '',
    category: 'MOBA',
    platform: 'MOBILE',
    sort_order: gamesStore.adminPagination.total,
    description: '',
    logo: null,
    error: '',
    submitting: false,
  }
}

function openGameEditModal(game) {
  gameModal.value = {
    mode: 'edit',
    id: game.id,
    name: game.name || '',
    english_name: game.english_name || '',
    category: game.category,
    platform: game.platform,
    sort_order: game.sort_order ?? 0,
    error: '',
    submitting: false,
  }
}

function closeGameModal() {
  gameModal.value = null
}

async function submitGameModal() {
  const state = gameModal.value
  if (!state) return
  state.error = ''

  if (!state.name.trim()) {
    state.error = '请填写游戏名称'
    return
  }

  state.submitting = true

  if (state.mode === 'create') {
    const result = await gamesStore.createGame({
      name: state.name.trim(),
      english_name: state.english_name.trim() || null,
      category: state.category,
      platform: state.platform,
      sort_order: Number(state.sort_order) || 0,
      description: state.description.trim() || null,
      service_template: buildDefaultServiceTemplate(state.category),
    })
    state.submitting = false
    if (!result.success) {
      state.error = formatApiError(result.error) || '创建失败'
      return
    }
    if (state.logo) {
      const logoResult = await gamesStore.uploadLogo(result.data.id, state.logo)
      if (!logoResult.success) {
        state.error = `游戏已创建，但 Logo 上传失败：${logoResult.error || '请稍后重试'}`
        state.submitting = false
        return
      }
    }
    message.value = { type: 'success', text: `已创建「${result.data.name}」（默认下架，需手动上架）` }
    closeGameModal()
    return
  }

  const result = await gamesStore.updateGame(state.id, {
    name: state.name.trim(),
    sort_order: Number(state.sort_order) || 0,
  })
  state.submitting = false
  if (!result.success) {
    state.error = formatApiError(result.error) || '保存失败'
    return
  }
  message.value = { type: 'success', text: `已更新「${result.data.name}」` }
  closeGameModal()
}

function openGameDeleteModal(game) {
  gameDeleteModal.value = {
    id: game.id,
    name: game.name,
    error: '',
    submitting: false,
  }
}

function closeGameDeleteModal() {
  gameDeleteModal.value = null
}

async function confirmDeleteGame() {
  const state = gameDeleteModal.value
  if (!state) return
  state.submitting = true
  const result = await gamesStore.deleteGame(state.id)
  state.submitting = false
  if (!result.success) {
    state.error = formatApiError(result.error) || '删除失败'
    return
  }
  message.value = { type: 'success', text: `已删除「${state.name}」` }
  closeGameDeleteModal()
}

async function toggleGameStatus(game) {
  submittingKey.value = `game-${game.id}`
  const result = await gamesStore.updateGame(game.id, { is_active: !game.is_active })
  if (result.success) {
    message.value = { type: 'success', text: game.is_active ? '已下架' : '已上架' }
  } else {
    message.value = { type: 'error', text: formatApiError(result.error) || '操作失败' }
  }
  submittingKey.value = ''
}

// 切到游戏管理 tab 时拉取最新全量列表（含未上架）
watch(activeTab, (tab) => {
  if (tab === 'games') {
    fetchGamesWithFilter()
  }
})

async function refreshDashboard() {
  await Promise.all([fetchApplications(), fetchOrders(), fetchGamesWithFilter(), fetchWithdrawals(1)])
}

onMounted(async () => {
  await refreshDashboard()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section v-if="activeTab !== 'orders'" class="admin-overview surface-card">
      <h1 class="text-lg font-semibold text-ink-1">运营管理台</h1>
      <div class="admin-overview__stats">
        <article v-for="item in dashboardStats" :key="item.label" class="admin-overview__stat">
          <strong>{{ item.value }}</strong><span>{{ item.label }}</span>
        </article>
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
          <button v-if="isAdmin" type="button" :class="activeTab === 'users' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'users'">用户管理</button>
          <button v-if="isAdmin" type="button" :class="activeTab === 'site' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'site'">站点管理</button>
        </nav>
        <button class="btn-secondary shrink-0 !px-4 !py-2" @click="refreshDashboard">刷新</button>
      </div>
    </section>

    <AdminDashboard v-if="activeTab === 'dashboard'" />
    <AdminUserList v-else-if="activeTab === 'users'" />
    <AdminSiteSettings v-else-if="activeTab === 'site'" />

    <section v-else-if="activeTab === 'applications'" class="surface-card p-6 sm:p-8">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <h2 class="text-2xl font-semibold text-ink-1">代练审核</h2>
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
                <h3 class="text-xl font-semibold text-ink-1">{{ item.username }}</h3>
                <span :class="applicationMeta(item.status).badgeClass">{{ applicationMeta(item.status).label }}</span>
              </div>
              <p class="mt-2 text-sm text-ink-2">{{ item.email }}</p>
              <p class="mt-2 text-sm text-ink-2">{{ item.game_name || '未填游戏' }} · {{ item.current_rank || '-' }} → {{ item.target_rank || '-' }}</p>
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
            <p class="text-xs text-ink-3">{{ getUserRoleLabel(item.role) }}</p>
            <button class="btn-primary !px-4 !py-2" :disabled="submittingKey === `review-${item.user_id}`" @click="submitReview(item.user_id)">
              {{ submittingKey === `review-${item.user_id}` ? '提交中...' : '提交' }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <section v-else-if="activeTab === 'orders'" class="surface-card admin-orders-panel">
      <div class="admin-orders-heading">
        <h2 class="text-xl font-semibold text-ink-1">订单管理</h2>
        <button v-if="isAdmin" class="btn-primary shrink-0 !px-5 !py-2" @click="openPublishModal">发布订单</button>
      </div>
      <div class="admin-orders-toolbar">
        <span class="text-sm text-ink-2">共 {{ orders.length }} 条记录</span>
        <div class="admin-bulk-actions" :class="{ 'is-empty': !selectedOrderIds.length }">
          <span v-if="selectedOrderIds.length" class="text-sm text-ink-2">已选 {{ selectedOrderIds.length }} 条</span>
          <button v-if="selectedOrderIds.length" class="btn-secondary !px-4 !py-2" @click="bulkOrderAction('pause')">批量暂停</button>
          <button v-if="selectedOrderIds.length" class="btn-secondary !px-4 !py-2" @click="bulkOrderAction('close')">批量截止</button>
          <button v-if="selectedOrderIds.length" class="btn-ghost !px-4 !py-2" @click="bulkOrderAction('archive')">批量归档</button>
        </div>
      </div>

      <div v-if="loadingOrders" class="mt-6 grid gap-4 xl:grid-cols-2" aria-busy="true">
        <div v-for="n in 4" :key="`order-skeleton-${n}`" class="skeleton h-44 !rounded-card"></div>
      </div>

      <div v-else-if="!orders.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">📦</div>
        <h3 class="empty-state__title">暂无订单</h3>
        <p class="empty-state__copy">有新订单进来后，会出现在这里等待处理。</p>
      </div>

      <div v-else class="mt-6 grid gap-4 xl:grid-cols-2">
        <article v-for="order in orders" :key="order.id" class="catalog-card admin-order-card cyber-corner" :class="order.claim_status === 'CLOSED' || order.is_archived || ['COMPLETED', 'CANCELLED', 'EXPIRED'].includes(order.status) ? 'admin-order-card--ended' : ''">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-xl font-semibold text-ink-1">#{{ order.id }} {{ order.game_name }}</h3>
                <span :class="getOrderStatusBadgeClass(order.status)">{{ getOrderStatusLabel(order.status) }}</span>
                <span v-if="order.payment_status" :class="paymentBadgeClass(order.payment_status)">{{ paymentLabel(order.payment_status) }}</span>
              </div>
              <p class="mt-2 text-sm text-ink-2">{{ order.current_rank }} → {{ order.target_rank }}</p>
            </div>
            <p class="text-lg font-semibold tabular-nums text-price">{{ formatPrice(order.price) }}</p>
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

<div class="mt-5 flex flex-wrap items-center justify-between gap-3 admin-order-footer">
              <label class="inline-flex min-h-[44px] items-center gap-2 text-sm text-ink-2"><input v-model="selectedOrderIds" type="checkbox" :value="order.id" /> 批量选择</label>
              <p class="text-xs text-ink-3">{{ formatDateTime(order.created_at) }}</p>
              <div class="admin-order-actions flex flex-wrap gap-2">
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
              <button v-if="order.claim_status === 'OPEN'" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="controlOrder(order.id, 'pause')">暂停</button>
              <button v-else-if="order.claim_status === 'PAUSED'" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="controlOrder(order.id, 'resume')">恢复</button>
              <button v-if="!['CLOSED'].includes(order.claim_status) && !order.is_archived" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="controlOrder(order.id, 'close')">截止</button>
              <button v-if="!order.is_archived" class="btn-ghost min-h-[44px] !px-4 !py-2" @click="controlOrder(order.id, 'archive')">归档</button>
              <button class="btn-danger min-h-[44px] !px-4 !py-2" @click="deleteOrder(order.id)">删除</button>
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
        <h2 class="text-2xl font-semibold text-ink-1">提现处理</h2>
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
                <h3 class="text-xl font-semibold text-ink-1">#{{ item.id }} {{ withdrawalApplicant(item) }}</h3>
                <span :class="['tag', getWithdrawalStatusTagClass(item.status)]">{{ getWithdrawalStatusLabel(item.status) }}</span>
              </div>
              <p class="text-sm text-ink-2">
                {{ getChannelLabel(item.channel) }} · {{ item.account_name || '-' }} · {{ item.account_no || '-' }}
              </p>
              <p class="text-xs text-ink-3">申请于 {{ formatDateTime(item.created_at) }}</p>
              <p v-if="item.paid_at" class="text-xs text-ink-3">打款于 {{ formatDateTime(item.paid_at) }}</p>
            </div>

            <p class="text-2xl font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</p>
          </div>

          <div
            v-if="item.status === 'REJECTED' && item.reject_reason"
            class="message-error mt-4"
          >
            驳回原因：{{ item.reject_reason }}
          </div>

          <div
            v-if="item.payment_reference"
            class="message-info mt-4"
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
        <p class="text-sm text-ink-2">
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
      <h2 class="text-2xl font-semibold text-ink-1">钱包调账</h2>
      <p class="mt-2 text-sm text-ink-2">为指定用户直接调整钱包余额：金额为正表示充值，为负表示扣减。</p>

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

    <section v-else-if="activeTab === 'games'" class="surface-card p-6 sm:p-8">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 class="text-2xl font-semibold text-ink-1">游戏管理</h2>
          <p class="mt-2 text-sm text-ink-2">
            全量 {{ gamesStore.adminPagination.total }} 款游戏（含未上架）· 对外游戏专区只展示已上架游戏。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <select v-model="gameStatusFilter" class="input min-w-[140px]" @change="fetchGamesWithFilter">
            <option v-for="option in gameStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
          <button v-if="selectedGameIds.length" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="bulkGameAction('activate')">批量上架</button>
          <button v-if="selectedGameIds.length" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="bulkGameAction('deactivate')">批量下架</button>
          <button v-if="selectedGameIds.length" class="btn-danger min-h-[44px] !px-4 !py-2" @click="bulkGameAction('delete')">批量删除</button>
          <button class="btn-primary min-h-[44px] shrink-0 !px-5 !py-2" @click="openGameCreateModal">新建游戏</button>
        </div>
      </div>

      <div v-if="loadingGames" class="mt-6 grid gap-4 xl:grid-cols-2" aria-busy="true">
        <div v-for="n in 6" :key="`game-skeleton-${n}`" class="skeleton h-28 !rounded-card"></div>
      </div>

      <div v-else-if="!gamesStore.adminGames.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">🎮</div>
        <h3 class="empty-state__title">{{ gameStatusFilter ? '该状态下暂无游戏' : '暂无游戏' }}</h3>
        <p class="empty-state__copy">
          {{ gameStatusFilter ? '换个状态筛选试试；也可以新建游戏（默认下架，需手动上架）。' : '点击右上角「新建游戏」创建第一款游戏；新游戏默认下架，需手动上架后才对外展示。' }}
        </p>
      </div>

      <div v-else class="mt-6 grid gap-4 xl:grid-cols-2">
        <article v-for="game in gamesStore.adminGames" :key="game.id" class="catalog-card cyber-corner">
          <div class="mb-3 flex items-center gap-3"><input v-model="selectedGameIds" type="checkbox" :value="game.id" aria-label="选择游戏" /><img v-if="game.logo_url" :src="game.logo_url" class="h-10 w-10 rounded-tile object-cover" alt="" /><span v-else class="flex h-10 w-10 items-center justify-center rounded-tile bg-surface-3 text-ink-2">{{ game.name.slice(0, 1) }}</span></div>
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="text-xl font-semibold text-ink-1">{{ game.name }}</h3>
                <span :class="game.is_active ? 'badge-approved' : 'badge-cancelled'">{{ game.is_active ? '已上架' : '未上架' }}</span>
              </div>
              <p class="mt-2 truncate text-sm text-ink-2">
                {{ getGameCategoryMeta(game.category).label }} · {{ getGamePlatformLabel(game.platform) }}<template v-if="game.english_name"> · {{ game.english_name }}</template>
              </p>
              <p v-if="game.description" class="mt-1.5 truncate text-[13px] text-ink-3">{{ game.description }}</p>
            </div>
            <p class="shrink-0 text-sm tabular-nums text-ink-3">排序 {{ game.sort_order }}</p>
          </div>

          <div class="mt-5 flex items-center justify-end gap-2">
            <label class="btn-ghost min-h-[44px] !px-4 !py-2"><input type="file" accept="image/*" class="sr-only" @change="handleLogoChange(game.id, $event)" />{{ submittingKey === `logo-${game.id}` ? '上传中...' : '上传 Logo' }}</label>
            <button
              class="btn-secondary min-h-[44px] !px-4 !py-2"
              :disabled="submittingKey === `game-${game.id}`"
              @click="toggleGameStatus(game)"
            >
              {{ submittingKey === `game-${game.id}` ? '处理中...' : (game.is_active ? '下架' : '上架') }}
            </button>
            <button class="btn-ghost !px-4 !py-2" @click="openGameEditModal(game)">编辑</button>
            <button class="btn-danger !px-4 !py-2" @click="openGameDeleteModal(game)">删除</button>
          </div>
        </article>
      </div>

      <div v-if="gamesStore.adminPagination.pages > 1" class="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm tabular-nums text-ink-2">
          {{ gamesStore.adminPagination.page }} / {{ gamesStore.adminPagination.pages }} · 共 {{ gamesStore.adminPagination.total }} 款
        </p>
        <div class="flex items-center gap-2">
          <button
            class="btn-secondary !px-4 !py-2"
            :disabled="gamesStore.adminPagination.page <= 1"
            @click="handleGamePageChange(gamesStore.adminPagination.page - 1)"
          >
            上一页
          </button>
          <button
            class="btn-secondary !px-4 !py-2"
            :disabled="gamesStore.adminPagination.page >= gamesStore.adminPagination.pages"
            @click="handleGamePageChange(gamesStore.adminPagination.page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </section>

    <div v-if="modal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="closeModal"></div>

      <div class="modal-card modal-sheet" role="dialog" aria-modal="true">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-ink-1">{{ modalTitle() }}</h3>
          <p class="mt-2 text-sm text-ink-2">
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

    <!-- 发布订单弹窗（管理员发单 → POST /orders/create，进大厅 PENDING） -->
    <div v-if="publishModal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="closePublishModal"></div>

      <div class="modal-card modal-sheet" role="dialog" aria-modal="true">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-ink-1">发布订单</h3>
          <p class="mt-2 text-sm text-ink-2">以管理员身份发单，订单进入大厅待接单，并自动通知打手。</p>

          <div v-if="publishModal.error" class="message-error mt-4">{{ publishModal.error }}</div>
          <div v-if="publishGameOptions.length && !hasActivePublishGame" class="message-info mt-4">
            当前所有游戏都未上架，建议先到「游戏管理」上架游戏；也可以直接选择未上架游戏发单。
          </div>
          <div v-else-if="!publishGameOptions.length" class="message-info mt-4">
            暂无游戏数据，请先在「游戏管理」中新建并上架游戏。
          </div>

          <form class="mt-5 space-y-4" @submit.prevent="submitPublishModal">
            <div>
              <label class="label" for="publish-game">游戏</label>
              <select id="publish-game" v-model="publishModal.game_id" class="input">
                <option :value="''" disabled>请选择游戏</option>
                <option v-for="game in publishGameOptions" :key="game.id" :value="game.id">
                  {{ game.name }}（{{ game.is_active ? '已上架' : '未上架' }}）
                </option>
              </select>
            </div>

            <div>
              <label class="label" for="publish-title">标题</label>
              <input id="publish-title" v-model="publishModal.title" class="input" maxlength="200" placeholder="例如：王者荣耀上分订单" />
            </div>
            <div>
              <label class="label" for="publish-intro">简介</label>
              <textarea id="publish-intro" v-model="publishModal.intro" rows="2" class="input resize-none" maxlength="5000"></textarea>
            </div>
            <div class="publish-grid grid gap-4 sm:grid-cols-2">
              <div>
                <label class="label" for="publish-current-rank">当前段位</label>
                <input
                  id="publish-current-rank"
                  v-model="publishModal.current_rank"
                  type="text"
                  class="input"
                  maxlength="50"
                  placeholder="例如：钻石"
                />
              </div>
              <div>
                <label class="label" for="publish-target-rank">目标段位</label>
                <input
                  id="publish-target-rank"
                  v-model="publishModal.target_rank"
                  type="text"
                  class="input"
                  maxlength="50"
                  placeholder="例如：王者"
                />
              </div>
            </div>

            <div class="publish-grid grid gap-4 sm:grid-cols-2">
              <div>
                <label class="label" for="publish-price">价格</label>
                <input
                  id="publish-price"
                  v-model="publishModal.price"
                  type="number"
                  min="0.01"
                  step="0.01"
                  class="input"
                  placeholder="建议 100"
                />
              </div>
              <div>
                <label class="label" for="publish-price-min">价格区间（可选）</label>
                <div class="flex gap-2"><input id="publish-price-min" v-model="publishModal.price_min" type="number" min="0.01" step="0.01" class="input" placeholder="最低" /><input v-model="publishModal.price_max" type="number" min="0.01" step="0.01" class="input" placeholder="最高" /></div>
              </div>
              <div>
                <label class="label" for="publish-max-claims">最大接单人数</label>
                <input id="publish-max-claims" v-model="publishModal.max_claims" type="number" min="1" max="100" class="input" />
              </div>
              <div>
                <label class="label" for="publish-deadline">截止时间</label>
                <input id="publish-deadline" v-model="publishModal.deadline" type="datetime-local" class="input" />
              </div>
              <div>
                <label class="label" for="publish-server">区服（可选）</label>
                <input
                  id="publish-server"
                  v-model="publishModal.server"
                  type="text"
                  class="input"
                  maxlength="50"
                  placeholder="例如：微信区"
                />
              </div>
            </div>

            <div>
              <p class="label">优先级</p>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="option in publishPriorityOptions"
                  :key="option.value"
                  type="button"
                  :class="publishModal.priority === option.value ? 'filter-pill-active' : 'filter-pill'"
                  @click="publishModal.priority = option.value"
                >
                  {{ option.label }}
                </button>
              </div>
            </div>

            <div>
              <label class="label" for="publish-attachments">图片附件（可选）</label>
              <input id="publish-attachments" type="file" accept="image/png,image/jpeg,image/webp" multiple class="input min-h-[44px]" @change="publishModal.attachments = $event.target.files" />
              <p class="mt-1 text-xs text-ink-3">最多5张，支持 PNG、JPEG、WebP，单张不超过5MB。</p>
              <p v-if="publishModal.uploadProgress" class="mt-2 text-sm text-primary">图片上传中：{{ publishModal.uploadProgress }}</p>
            </div>
            <div>
              <label class="label" for="publish-description">需求描述</label>
              <textarea
                id="publish-description"
                v-model="publishModal.description"
                rows="3"
                class="input resize-none"
                maxlength="2000"
                placeholder="补充给打手的要求，例如上线时间、沟通方式等（选填）"
              ></textarea>
            </div>

            <div class="flex justify-end gap-3 pt-2">
              <button type="button" class="btn-ghost !px-4 !py-2" @click="closePublishModal">取消</button>
              <button type="submit" class="btn-primary !px-5 !py-2" :disabled="publishModal.submitting">
                {{ publishModal.submitting ? '发布中...' : '发布到大厅' }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- 新建 / 编辑游戏弹窗 -->
    <div v-if="gameModal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="closeGameModal"></div>

      <div class="modal-card modal-sheet" role="dialog" aria-modal="true">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-ink-1">{{ gameModal.mode === 'create' ? '新建游戏' : '编辑游戏' }}</h3>
          <p v-if="gameModal.mode === 'create'" class="mt-2 text-sm text-ink-2">
            新建默认下架，需手动上架后才会在对外游戏专区展示。
          </p>
          <p v-else class="mt-2 text-sm text-ink-2">
            #{{ gameModal.id }} · {{ getGameCategoryMeta(gameModal.category).label }} / {{ getGamePlatformLabel(gameModal.platform) }}<template v-if="gameModal.english_name"> · {{ gameModal.english_name }}</template>
          </p>

          <div v-if="gameModal.error" class="message-error mt-4">{{ gameModal.error }}</div>

          <form class="mt-5 space-y-4" @submit.prevent="submitGameModal">
            <div>
              <label class="label" for="game-name">名称</label>
              <input
                id="game-name"
                v-model="gameModal.name"
                type="text"
                class="input"
                maxlength="100"
                placeholder="游戏中文名，例如：三角洲行动"
              />
            </div>

            <template v-if="gameModal.mode === 'create'">
              <div>
                <label class="label" for="game-english-name">英文名（可选）</label>
                <input
                  id="game-english-name"
                  v-model="gameModal.english_name"
                  type="text"
                  class="input"
                  maxlength="150"
                  placeholder="例如：Delta Force"
                />
              </div>

              <div class="publish-grid grid gap-4 sm:grid-cols-2">
                <div>
                  <label class="label" for="game-category">分类</label>
                  <select id="game-category" v-model="gameModal.category" class="input">
                    <option v-for="option in gameCategoryOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </div>
                <div>
                  <label class="label" for="game-platform">平台</label>
                  <select id="game-platform" v-model="gameModal.platform" class="input">
                    <option v-for="option in gamePlatformOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                </div>
              </div>

              <div>
                <label class="label" for="game-logo">Logo（可选）</label>
                <input id="game-logo" type="file" accept="image/*" class="input min-h-[44px]" @change="gameModal.logo = $event.target.files?.[0] || null" />
              </div>

              <div>
                <label class="label" for="game-description">一句话简介（可选）</label>
                <input
                  id="game-description"
                  v-model="gameModal.description"
                  type="text"
                  class="input"
                  maxlength="100"
                  placeholder="展示在游戏卡片上的简介"
                />
              </div>
            </template>

            <div>
              <label class="label" for="game-sort-order">排序号（越小越靠前）</label>
              <input
                id="game-sort-order"
                v-model.number="gameModal.sort_order"
                type="number"
                min="0"
                step="1"
                class="input"
              />
            </div>

            <div class="flex justify-end gap-3 pt-2">
              <button type="button" class="btn-ghost !px-4 !py-2" @click="closeGameModal">取消</button>
              <button type="submit" class="btn-primary !px-5 !py-2" :disabled="gameModal.submitting">
                {{ gameModal.submitting ? '保存中...' : (gameModal.mode === 'create' ? '创建' : '保存') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- 删除游戏确认弹窗 -->
    <div v-if="gameDeleteModal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="closeGameDeleteModal"></div>

      <div class="modal-card modal-sheet" role="dialog" aria-modal="true">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-ink-1">删除游戏</h3>
          <p class="mt-2 text-sm text-ink-2">
            确认删除「{{ gameDeleteModal.name }}」？关联订单的游戏引用会被置空，该操作不可恢复。
          </p>

          <div v-if="gameDeleteModal.error" class="message-error mt-4">{{ gameDeleteModal.error }}</div>

          <div class="mt-5 flex justify-end gap-3">
            <button type="button" class="btn-ghost !px-4 !py-2" @click="closeGameDeleteModal">取消</button>
            <button
              type="button"
              class="btn-danger !px-5 !py-2"
              :disabled="gameDeleteModal.submitting"
              @click="confirmDeleteGame"
            >
              {{ gameDeleteModal.submitting ? '删除中...' : '确认删除' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
