<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import AdminDashboard from '@/components/admin/AdminDashboard.vue'
import AdminUserList from '@/components/admin/AdminUserList.vue'
import AdminSiteSettings from '@/components/admin/AdminSiteSettings.vue'
import Lightbox from '@/components/Lightbox.vue'
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
import { formatDateTime, formatOrderPrice, formatPrice } from '@/utils/display'
import {
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
const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)
const gamesStore = useGamesStore()
const ordersStore = useOrdersStore()
const walletStore = useWalletStore()

const TAB_KEYS = ['dashboard', 'orders', 'withdrawals', 'wallet-adjust', 'games', 'users', 'site']

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

const orders = ref([])
const loadingOrders = ref(false)
const loadingGames = computed(() => gamesStore.adminLoading)
const loadingWithdrawals = computed(() => walletStore.adminWithdrawalsLoading)
const message = ref({ type: '', text: '' })
const submittingKey = ref('')

const withdrawalStatus = ref('PENDING')
const adjustForm = ref({ user_id: '', amount: '', reason: '' })
const adjustMessage = ref({ type: '', text: '' })
// 弹窗：assign（派单）/ reject（驳回提现）/ mark-paid（标记已打款）
const modal = ref(null)

const dashboardStats = computed(() => [
  { label: '待审核', value: orders.value.filter((order) => order.status === 'DELIVERED').length },
  { label: '派单', value: orders.value.length },
  { label: '游戏', value: gamesStore.adminGames.length },
])

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
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

async function bulkOrderAction(action) {
  if (!selectedOrderIds.value.length) return
  for (const id of selectedOrderIds.value) await controlOrder(id, action)
  selectedOrderIds.value = []
  await fetchOrders()
}

// 卡片点击进入派单处理详情页（编辑 / 接单名单 / 审核 / 退款 / 干预均已迁移）
function goDispatchDetail(order) {
  router.push({ name: 'admin-dispatch-detail', params: { id: order.id } })
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

// ── 提现详情弹窗：卡片点击查看申请全量信息 + 收款二维码 ──

const withdrawalDetail = ref(null)

function openWithdrawalDetail(item) {
  withdrawalDetail.value = item
}

function closeWithdrawalDetail() {
  withdrawalDetail.value = null
}

// 复用订单灯箱展示收款二维码（点击放大）
function openWithdrawalQrcode(item) {
  if (!item?.qrcode_url) return
  orderLightbox.value = { visible: true, images: [{ url: item.qrcode_url, name: '收款二维码' }], index: 0 }
}

async function approveWithdrawal(item) {
  submittingKey.value = `withdrawal-${item.id}`
  const result = await walletStore.reviewWithdrawal(item.id, 'approve')
  if (result.success) {
    message.value = { type: 'success', text: '提现申请已通过，等待打款' }
    if (withdrawalDetail.value?.id === item.id) closeWithdrawalDetail()
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
  closeWithdrawalDetail()
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
  closeWithdrawalDetail()
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
const maxAttachmentSize = 10 * 1024 * 1024

function validateOrderAttachments(files) {
  const selected = Array.from(files || [])
  if (selected.length > maxAttachmentCount) return '订单最多上传5张图片'
  const invalid = selected.find((file) => !attachmentTypes.includes((file.type || '').toLowerCase()))
  if (invalid) return `仅支持 PNG、JPEG、WebP 图片：${invalid.name}`
  const oversized = selected.find((file) => file.size > maxAttachmentSize)
  if (oversized) return `单张图片不能超过10MB：${oversized.name}`
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

const ADMIN_PUBLISH_SERVICE_TYPE = '陪玩'

// 到账时效：不设置（默认）/ 1-5 天 → payout_delay_days
const publishPayoutDelayOptions = [
  { value: '', label: '不设置' },
  { value: 1, label: '1天' },
  { value: 2, label: '2天' },
  { value: 3, label: '3天' },
  { value: 4, label: '4天' },
  { value: 5, label: '5天' },
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
    price: '100',
    max_claims: 1,
    deadline: '',
    attachments: null,
    description: '',
    service_type: ADMIN_PUBLISH_SERVICE_TYPE,
    server: '',
    boss_contact: '',
    compensation_enabled: false,
    compensation_amount: '',
    payout_delay_days: '',
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
  // 价格：只发单一 {price}（区间价格已下线）
  const price = Number(state.price)
  if (!Number.isFinite(price) || price <= 0) {
    state.error = '请填写大于 0 的价格'
    return
  }

  // 新增字段校验：老板ID ≤64 字；赔偿金开启后必须 >0；到账时效 1-5 天
  const bossContact = state.boss_contact.trim()
  if (bossContact.length > 64) {
    state.error = '老板ID不能超过 64 个字符'
    return
  }

  let compensationAmount = null
  if (state.compensation_enabled) {
    compensationAmount = Number(state.compensation_amount)
    if (!Number.isFinite(compensationAmount) || compensationAmount <= 0) {
      state.error = '炸单赔偿金需为大于 0 的金额'
      return
    }
  }

  const payoutDelay = state.payout_delay_days === '' ? null : Number(state.payout_delay_days)
  if (payoutDelay != null && (!Number.isInteger(payoutDelay) || payoutDelay < 1 || payoutDelay > 5)) {
    state.error = '到账时效需为 1-5 天'
    return
  }

  state.submitting = true
  const payload = {
    game_id: game.id,
    game_name: game.name,
    title: state.title.trim() || `${game.name} 派单`,
    intro: state.intro.trim() || state.description.trim() || null,
    price,
    max_claims: Number(state.max_claims) || 1,
    deadline: state.deadline || null,
    description_raw: state.description.trim() || null,
    service_type: ADMIN_PUBLISH_SERVICE_TYPE,
    server: state.server.trim() || null,
    boss_contact: bossContact || null,
    payout_delay_days: payoutDelay,
  }
  if (state.compensation_enabled) {
    payload.compensation_amount = compensationAmount
  }
  const result = await ordersStore.createOrder(payload)
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

// ── 订单卡片展示（标题 / 简介 / 价格 / 名额 / 附件缩略图 + Lightbox）──

function orderCardTitle(order) {
  return order.title || `#${order.id} ${order.game_name}`
}

function buildOrderSummary(order) {
  if (order.intro) return order.intro
  const detail = order.ai_tags?.detail || {}
  const requirements = Array.isArray(detail.requirements) ? detail.requirements.filter(Boolean) : []
  const pieces = [order.service_type, order.server, detail.role, requirements[0]].filter(Boolean)
  if (pieces.length) return pieces.join(' · ')
  const raw = order.description_raw || order.description || '未补充需求'
  return raw.length > 28 ? `${raw.slice(0, 28)}...` : raw
}

function orderPriceLabel(order) {
  return formatOrderPrice(order)
}

function orderClaimLabel(order) {
  return `当前情况 ${Number(order.claimed_count ?? 0)}/${Number(order.max_claims ?? 0)}`
}

// 发布人（用户发单时序列化带 user；管理员发单无托管）
function orderPublisher(order) {
  return order.user?.username || (order.user_id != null ? `用户 #${order.user_id}` : '')
}

// 区分来源：管理员自己派的 vs 普通用户派的
function isUserPublished(order) {
  return order.user?.role && order.user.role !== 'ADMIN'
}

function orderEscrowLabel(order) {
  const amount = Number(order.escrow_amount ?? 0)
  return amount > 0 ? `托管 ${formatPrice(amount)}` : ''
}

// 附件归一化：元素为 {url,name} 对象（后端 OrderAttachment）或历史遗留字符串
function normalizeOrderAttachments(value) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (typeof item === 'string' ? { url: item, name: '' } : { url: item?.url || '', name: item?.name || '' }))
    .filter((item) => item.url)
}

const orderLightbox = ref({ visible: false, images: [], index: 0 })

function openOrderLightbox(order, index) {
  orderLightbox.value = { visible: true, images: normalizeOrderAttachments(order.attachments), index }
}

// DELIVERED 在派单语境下是"待老板审核"
function dispatchStatusLabel(order) {
  return order.status === 'DELIVERED' ? '待审核' : getOrderStatusLabel(order.status)
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
  await Promise.all([fetchOrders(), fetchGamesWithFilter(), fetchWithdrawals(1)])
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
          <button type="button" :class="activeTab === 'orders' ? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'orders'">派单管理</button>
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

    <section v-else-if="activeTab === 'orders'" class="surface-card admin-orders-panel">
      <div class="admin-orders-heading">
        <h2 class="text-xl font-semibold text-ink-1">派单列表</h2>
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
        <article v-for="order in orders" :key="order.id" class="catalog-card admin-order-card cyber-corner cursor-pointer" :class="order.claim_status === 'CLOSED' || order.is_archived || ['COMPLETED', 'CANCELLED', 'EXPIRED'].includes(order.status) ? 'admin-order-card--ended' : ''" @click="goDispatchDetail(order)">
          <!-- 首行：订单标题 + 状态徽标；次行 #id · 游戏；再一行简介摘要 -->
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="min-w-0 truncate text-lg font-semibold text-ink-1">{{ orderCardTitle(order) }}</h3>
              <span :class="getOrderStatusBadgeClass(order.status)">{{ dispatchStatusLabel(order) }}</span>
            </div>
            <p class="mt-1 truncate text-xs text-ink-3">
              #{{ order.id }} · {{ order.game_name }}
            </p>
            <!-- 发布人 + 来源标签 + 托管金额：一眼看出是管理员派的单还是哪个用户派的单 -->
            <p v-if="orderPublisher(order) || orderEscrowLabel(order)" class="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
              <span v-if="isUserPublished(order)" class="tag !px-2 !py-0.5 !bg-primary-soft !text-primary">用户派单</span>
              <span v-else class="tag !px-2 !py-0.5">管理员派单</span>
              <span v-if="orderPublisher(order)">发布人：{{ orderPublisher(order) }}</span>
              <span v-if="orderEscrowLabel(order)" class="tag tabular-nums !px-2 !py-0.5">{{ orderEscrowLabel(order) }}</span>
            </p>
            <p class="mt-1.5 truncate text-sm text-ink-2">{{ buildOrderSummary(order) }}</p>
          </div>

          <!-- 数据行：红色价格 + 当前情况 X/Y 胶囊 + 审核计数 -->
          <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
            <p class="text-lg font-semibold tabular-nums text-price">{{ orderPriceLabel(order) }}</p>
            <div class="flex flex-wrap items-center gap-2">
              <span class="tag tabular-nums">{{ orderClaimLabel(order) }}</span>
              <span v-if="Number(order.pending_review_count) > 0" class="tag tabular-nums !bg-warning-soft !text-warning">待审核 {{ order.pending_review_count }}</span>
              <span v-if="Number(order.settled_count) > 0" class="tag tabular-nums !bg-success-soft !text-success">已通过 {{ order.settled_count }}</span>
            </div>
          </div>

          <!-- 图片缩略条：点击 Lightbox 查看大图 -->
          <div v-if="normalizeOrderAttachments(order.attachments).length" class="admin-order-thumbs mt-3 flex gap-2">
            <button
              v-for="(image, index) in normalizeOrderAttachments(order.attachments)"
              :key="image.url"
              type="button"
              class="admin-order-thumb"
              :aria-label="`查看订单图片 ${index + 1}`"
              @click.stop="openOrderLightbox(order, index)"
            >
              <img :src="image.url" :alt="image.name || '订单图片'" loading="lazy" />
            </button>
          </div>

          <div class="mt-5 flex flex-wrap items-center justify-between gap-3 admin-order-footer">
            <label class="inline-flex min-h-[44px] items-center gap-2 text-sm text-ink-2" @click.stop><input v-model="selectedOrderIds" type="checkbox" :value="order.id" /> 批量选择</label>
            <p class="text-xs text-ink-3">{{ formatDateTime(order.created_at) }}</p>
            <button type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" @click.stop="goDispatchDetail(order)">进入处理 →</button>
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
        <article
          v-for="item in walletStore.adminWithdrawals"
          :key="item.id"
          class="catalog-card cyber-corner cursor-pointer transition-colors duration-base hover:border-primary"
          @click="openWithdrawalDetail(item)"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0 max-w-full space-y-2">
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

            <div class="flex shrink-0 flex-col items-end gap-3">
              <p class="text-2xl font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</p>
              <!-- 收款二维码缩略图（点击在弹窗内放大） -->
              <img
                v-if="item.qrcode_url"
                :src="item.qrcode_url"
                alt="收款二维码"
                class="h-14 w-14 rounded-tile border border-line-1 object-cover"
                loading="lazy"
              />
              <span v-else class="text-xs text-ink-3">未上传二维码</span>
            </div>
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

          <p class="mt-4 text-xs text-ink-3">点击卡片查看详情并处理 →</p>
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

    <!-- 提现详情弹窗：申请全量信息 + 收款二维码 + 处理按钮 -->
    <div v-if="withdrawalDetail" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="closeWithdrawalDetail"></div>

      <div class="modal-card modal-sheet" role="dialog" aria-modal="true" aria-label="提现详情">
        <div class="relative z-10">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-2xl font-semibold text-ink-1">提现详情</h3>
              <p class="mt-2 text-sm text-ink-2">
                #{{ withdrawalDetail.id }} · {{ withdrawalApplicant(withdrawalDetail) }} ·
                <span class="font-semibold tabular-nums text-price">{{ formatPrice(withdrawalDetail.amount) }}</span>
              </p>
            </div>
            <button type="button" class="btn-ghost shrink-0 !px-4 !py-2" @click="closeWithdrawalDetail">关闭</button>
          </div>

          <div class="mt-5 grid gap-3 sm:grid-cols-2">
            <div class="info-tile">
              <p class="info-tile__label">状态</p>
              <p class="info-tile__value"><span :class="['tag', getWithdrawalStatusTagClass(withdrawalDetail.status)]">{{ getWithdrawalStatusLabel(withdrawalDetail.status) }}</span></p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">收款渠道</p>
              <p class="info-tile__value">{{ getChannelLabel(withdrawalDetail.channel) }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">收款人姓名</p>
              <p class="info-tile__value break-words">{{ withdrawalDetail.account_name || '-' }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">收款账号</p>
              <p class="info-tile__value break-words">{{ withdrawalDetail.account_no || '-' }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">申请时间</p>
              <p class="info-tile__value">{{ formatDateTime(withdrawalDetail.created_at) }}</p>
            </div>
            <div v-if="withdrawalDetail.paid_at" class="info-tile">
              <p class="info-tile__label">打款时间</p>
              <p class="info-tile__value">{{ formatDateTime(withdrawalDetail.paid_at) }}</p>
            </div>
          </div>

          <div v-if="withdrawalDetail.status === 'REJECTED' && withdrawalDetail.reject_reason" class="message-error mt-4">
            驳回原因：{{ withdrawalDetail.reject_reason }}
          </div>
          <div v-if="withdrawalDetail.payment_reference" class="message-info mt-4">
            打款流水号：{{ withdrawalDetail.payment_reference }}
          </div>

          <!-- 收款二维码：点击用灯箱放大 -->
          <div class="mt-5">
            <p class="info-tile__label">收款二维码</p>
            <button
              v-if="withdrawalDetail.qrcode_url"
              type="button"
              class="mt-2 block overflow-hidden rounded-tile border border-line-1 bg-surface-2 transition-opacity duration-base hover:opacity-90"
              aria-label="放大查看收款二维码"
              @click="openWithdrawalQrcode(withdrawalDetail)"
            >
              <img :src="withdrawalDetail.qrcode_url" alt="收款二维码" class="h-40 w-40 object-cover" loading="lazy" />
            </button>
            <p v-else class="mt-2 text-sm text-ink-3">未上传二维码</p>
          </div>

          <div class="mt-6 flex flex-wrap justify-end gap-3">
            <button
              v-if="withdrawalDetail.status === 'PENDING'"
              type="button"
              class="btn-primary !px-5 !py-2"
              :disabled="submittingKey === `withdrawal-${withdrawalDetail.id}`"
              @click="approveWithdrawal(withdrawalDetail)"
            >
              {{ submittingKey === `withdrawal-${withdrawalDetail.id}` ? '提交中...' : '通过' }}
            </button>
            <button
              v-if="withdrawalDetail.status === 'PENDING'"
              type="button"
              class="btn-secondary !px-5 !py-2"
              @click="openRejectModal(withdrawalDetail)"
            >
              驳回
            </button>
            <button
              v-if="withdrawalDetail.status === 'APPROVED'"
              type="button"
              class="btn-primary !px-5 !py-2"
              @click="openMarkPaidModal(withdrawalDetail)"
            >
              标记已打款
            </button>
          </div>
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
            <div>
              <label class="label" for="publish-service-type">服务类型</label>
              <input id="publish-service-type" v-model="publishModal.service_type" class="input" type="text" disabled />
            </div>
            <!-- 价格：只保留一个发单价格输入（区间价格已下线） -->
            <div>
              <label class="label" for="publish-price">发单价格</label>
              <input
                id="publish-price"
                v-model="publishModal.price"
                type="number"
                min="0.01"
                step="0.01"
                class="input"
                placeholder="例如 60"
              />
            </div>

            <div class="publish-grid grid gap-4 sm:grid-cols-2">
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
              <div>
                <label class="label" for="publish-payout-delay">到账时效</label>
                <select id="publish-payout-delay" v-model="publishModal.payout_delay_days" class="input">
                  <option v-for="option in publishPayoutDelayOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </div>
              <div class="sm:col-span-2">
                <label class="label" for="publish-boss-contact">老板ID（可选）</label>
                <input
                  id="publish-boss-contact"
                  v-model="publishModal.boss_contact"
                  type="text"
                  class="input"
                  maxlength="64"
                  placeholder="接单后打手可见，用于添加你为好友"
                />
              </div>
            </div>

            <!-- 炸单赔偿金：开关默认关，开启后填写金额（>0）；未开启提交不带 compensation_amount -->
            <div class="rounded-tile border border-line-1 p-4">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p class="text-sm font-semibold text-ink-1">炸单赔偿金</p>
                  <p class="mt-1 text-xs leading-5 text-ink-3">打手接单时冻结，订单完结自动返还；炸单可由管理员扣除。</p>
                </div>
                <button
                  type="button"
                  :class="publishModal.compensation_enabled ? 'filter-pill-active' : 'filter-pill'"
                  :aria-pressed="publishModal.compensation_enabled"
                  @click="publishModal.compensation_enabled = !publishModal.compensation_enabled"
                >
                  {{ publishModal.compensation_enabled ? '已开启' : '未开启' }}
                </button>
              </div>
              <div v-if="publishModal.compensation_enabled" class="mt-3">
                <label class="label" for="publish-compensation">赔偿金额</label>
                <input id="publish-compensation" v-model="publishModal.compensation_amount" type="number" min="0.01" step="0.01" class="input" placeholder="例如 50" />
              </div>
            </div>

            <div>
              <label class="label" for="publish-attachments">图片附件（可选）</label>
              <input id="publish-attachments" type="file" accept="image/png,image/jpeg,image/webp" multiple class="input min-h-[44px]" @change="publishModal.attachments = $event.target.files" />
              <p class="mt-1 text-xs text-ink-3">最多5张，支持 PNG、JPEG、WebP，单张不超过10MB。</p>
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

    <!-- 订单图片大图预览（z-index 120，高于弹窗） -->
    <Lightbox
      :images="orderLightbox.images"
      :visible="orderLightbox.visible"
      :start-index="orderLightbox.index"
      @close="orderLightbox.visible = false"
    />

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

<style scoped>
/* ── 订单卡片图片缩略条：56-64px 圆角小图，横向一排可滚动 ── */
.admin-order-thumbs {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  overscroll-behavior-x: contain;
  padding-bottom: 2px;
}

.admin-order-thumb {
  flex: 0 0 auto;
  width: 60px;
  height: 60px;
  padding: 0;
  overflow: hidden;
  border-radius: 14px;
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  cursor: zoom-in;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.admin-order-thumb:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-card-hover);
}

.admin-order-thumb:active {
  transform: scale(0.94);
}

.admin-order-thumb img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* ── 编辑弹窗：已有附件缩略图 + 右上角删除按钮 ── */
.admin-edit-thumb {
  display: block;
  width: 60px;
  height: 60px;
  border-radius: 14px;
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  object-fit: cover;
}

.admin-edit-thumb-remove {
  position: absolute;
  top: -7px;
  right: -7px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 9999px;
  border: none;
  background: var(--danger);
  color: #fff;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.admin-edit-thumb-remove:hover {
  opacity: 0.88;
}

.admin-edit-thumb-remove:active {
  transform: scale(0.92);
}

.admin-edit-thumb-remove:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── 接单名单条目：主题表面色卡片，双主题可读 ── */
.claims-item {
  border-radius: 14px;
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  padding: 12px;
}
</style>
