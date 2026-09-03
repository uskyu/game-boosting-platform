<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import Lightbox from '@/components/Lightbox.vue'
import OrderDeliverModal from '@/components/OrderDeliverModal.vue'
import OrderTimeline from '@/components/OrderTimeline.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useOrdersStore } from '@/stores/orders'
import { getGameImage } from '@/data/gameImages'
import api from '@/utils/api'
import { formatDateTime, formatOrderPrice, formatPayoutDelay, formatPrice, formatShortDate } from '@/utils/display'
import { getClaimStatusMeta, getOrderStatusBadgeClass, getOrderStatusLabel, getOrderStatusMeta, getHumanStatusLabel, getHumanStatusSubtitle } from '@/utils/order'

const props = defineProps({
  id: {
    type: [String, Number],
    required: true,
  },
})

const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const ordersStore = useOrdersStore()

const errorMessage = ref('')
const successMessage = ref('')
const actionLoading = ref(false)
const chatLoading = ref(false)
const reviews = ref([])
const reviewForm = ref({ rating: 5, content: '' })
const editingReview = ref(false)
const confirmSuccess = ref(false)
const showDeliverModal = ref(false)
// 两步抢单：详情页先弹「接手订单」，确认后才调 acceptOrder
const showClaimModal = ref(false)
// 灯箱：订单画廊 / 交付附件各自独立索引
const orderLightboxVisible = ref(false)
const orderLightboxIndex = ref(0)
const deliveryLightboxVisible = ref(false)
const deliveryLightboxIndex = ref(0)

const order = computed(() => ordersStore.currentOrder)
const loading = computed(() => ordersStore.loading)
const currentUser = computed(() => authStore.user)
const isBooster = computed(() => authStore.isBooster)
const isOwner = computed(() => order.value?.user_id === currentUser.value?.id)
const isAssignedBooster = computed(() => order.value?.booster_id === currentUser.value?.id)
const isAdmin = computed(() => authStore.isAdmin)
const chatTargetUserId = computed(() => {
  if (!order.value || !currentUser.value) {
    return null
  }
  if (isOwner.value) {
    return order.value.booster_id || null
  }
  if (isAssignedBooster.value) {
    return order.value.user_id || null
  }
  // 代练在 PENDING 状态下也可以和老板聊（接单前沟通）
  if (isBooster.value && !isOwner.value && order.value.status === 'PENDING') {
    return order.value.user_id || null
  }
  return null
})
const canStartChat = computed(() => chatTargetUserId.value != null)
const statusMeta = computed(() => getOrderStatusMeta(order.value?.status))
const viewRole = computed(() => isAssignedBooster.value || (isBooster.value && !isOwner.value) ? 'booster' : 'owner')
const humanStatusLabel = computed(() => getHumanStatusLabel(order.value?.status, order.value?.service_type, viewRole.value))
const humanStatusSubtitle = computed(() => {
  // 打手视角有报名单时，副标题按 my_claim.status 描述审核流
  if (myClaim.value && viewRole.value === 'booster' && !isOwner.value) {
    const map = {
      CLAIMED: '完成后点击「结束订单」提交汇报',
      DELIVERED: '已提交汇报，等待订单发布人审核打款',
      SETTLED: '报酬已结算，已计入钱包余额',
    }
    return map[myClaim.value.status] ?? ''
  }
  return getHumanStatusSubtitle(order.value?.status, order.value?.service_type, viewRole.value)
})
const isPending = computed(() => order.value?.status === 'PENDING')
// 统计卡只显示已填写的参数：未填服务/区服直接不渲染，节省 UI
const detailStats = computed(() => {
  const o = order.value || {}
  const items = []
  if (o.service_type) items.push({ icon: 'S', label: '服务', value: o.service_type })
  if (o.server) items.push({ icon: 'R', label: '区服', value: o.server })
  items.push({ icon: '$', label: '金额', value: formatOrderPrice(o), valueClass: 'text-price' })
  items.push({ icon: 'T', label: '发布时间', value: formatShortDate(o.created_at) })
  return items
})
const isLocked = computed(() => order.value?.status === 'LOCKED')
const isDelivered = computed(() => order.value?.status === 'DELIVERED')
const isBoostOrder = computed(() => order.value?.service_type === '代练')
const heroStyle = computed(() => {
  const visual = getGameImage(order.value?.game_name)
  // 刮层走主题变量：亮色白纱 / 暗色黑纱，语义文字在两态都可读
  return {
    backgroundImage: visual.hero
      ? `linear-gradient(115deg, var(--scrim-strong) 0%, var(--scrim-mid) 60%, var(--scrim-soft) 100%), url('${visual.hero}')`
      : 'var(--surface-3)',
    backgroundPosition: 'center',
    backgroundSize: 'cover',
  }
})

const deadlineRemaining = computed(() => {
  const d = order.value?.deadline
  if (!d) return ''
  const t = new Date(d)
  if (Number.isNaN(t.getTime())) return formatDateTime(d)
  const diff = t.getTime() - Date.now()
  if (diff <= 0) return `已截止 ${formatDateTime(d)}`
  const h = Math.floor(diff / 3600000)
  const days = Math.floor(h / 24)
  if (days >= 1) return `剩余 ${days}天${h % 24}小时`
  if (h >= 1) return `剩余 ${h}小时`
  const mins = Math.max(1, Math.floor(diff / 60000))
  return `剩余 ${mins}分钟`
})

const isDeadlineOverdue = computed(() => {
  const d = order.value?.deadline
  if (!d) return false
  const t = new Date(d)
  return !Number.isNaN(t.getTime()) && t.getTime() <= Date.now()
})

const deliveryAttachments = computed(() => {
  // 优先读自己的报名单（my_claim），老数据回退订单级字段
  const v = myClaim.value?.delivery_attachments ?? order.value?.delivery_attachments
  return Array.isArray(v) ? v : []
})

const deliveryNoteText = computed(() => myClaim.value?.delivery_note ?? order.value?.delivery_note ?? '')

const orderAttachments = computed(() => {
  const v = order.value?.attachments
  return Array.isArray(v) ? v : []
})

// 我的报名单：订单序列化附带 my_claim；老数据回退到报名名单里自己的那条
const myClaim = computed(() => {
  if (order.value?.my_claim) return order.value.my_claim
  if (!order.value || !currentUser.value) return null
  const orderId = Number(order.value.id)
  return ordersStore.claims.find((claim) =>
    (claim?.order_id == null || Number(claim.order_id) === orderId) &&
    Number(claim?.booster_id) === Number(currentUser.value.id)
  ) || null
})

// 我的报名单是否已提交汇报（DELIVERED/SETTLED）；无报名单时回退订单级 DELIVERED
const isClaimDelivered = computed(() => {
  if (myClaim.value) return ['DELIVERED', 'SETTLED'].includes(myClaim.value.status)
  return isDelivered.value
})

// 一键复制老板ID（接单打手加好友用）
const bossContactCopied = ref(false)
async function copyBossContact() {
  const text = order.value?.boss_contact
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const el = document.createElement('textarea')
    el.value = text
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
  bossContactCopied.value = true
  window.setTimeout(() => { bossContactCopied.value = false }, 2000)
}

// 打手自己的状态标签按 my_claim.status 显示
const heroStatusClass = computed(() => {
  if (myClaim.value && viewRole.value === 'booster' && !isOwner.value) {
    return getClaimStatusMeta(myClaim.value.status).tagClass
  }
  return getOrderStatusBadgeClass(order.value?.status)
})

const heroStatusLabel = computed(() => {
  if (myClaim.value && viewRole.value === 'booster' && !isOwner.value) {
    return getClaimStatusMeta(myClaim.value.status).label
  }
  return humanStatusLabel.value
})

// 已报名：当前用户是接单人（booster_id），或出现在报名名单（claims）里
const hasClaimed = computed(() => {
  if (!order.value || !currentUser.value) {
    return false
  }
  if (isAssignedBooster.value) {
    return true
  }
  const orderId = Number(order.value.id)
  return ordersStore.claims.some((claim) => {
    if (claim?.order_id != null && Number(claim.order_id) !== orderId) return false
    return Number(claim?.booster_id) === Number(currentUser.value.id)
  })
})

// 接手订单弹窗正文用的订单标题（标题缺省时回退游戏名）
const claimSubject = computed(() => {
  if (!order.value) return ''
  return order.value.title || order.value.game_name || '代练订单'
})

// 可提交结束汇报：报名单进行中；老数据回退接单人 + 订单进行中
const canDeliver = computed(() => {
  if (isOwner.value) return false
  if (myClaim.value) return myClaim.value.status === 'CLAIMED'
  return isAssignedBooster.value && isLocked.value
})

const canReview = computed(() => {
  if (!order.value || !currentUser.value) {
    return false
  }
  return order.value.user_id === currentUser.value.id || order.value.booster_id === currentUser.value.id
})

const hasReviewed = computed(() => {
  return reviews.value.some((review) => review.reviewer_id === currentUser.value?.id)
})

function compactSummary() {
  const detail = order.value?.ai_tags?.detail || {}
  const requirements = Array.isArray(detail.requirements) ? detail.requirements.filter(Boolean) : []
  const items = [
    detail.role,
    requirements[0],
    order.value?.description_raw,
  ].filter(Boolean)

  const summary = items[0] || '未补充需求'
  return summary.length > 36 ? `${summary.slice(0, 36)}...` : summary
}

function attachmentUrl(attachment) {
  return typeof attachment === 'string' ? attachment : attachment?.url || ''
}

function attachmentName(attachment) {
  return typeof attachment === 'string' ? '' : attachment?.name || ''
}

function openOrderLightbox(index) {
  orderLightboxIndex.value = index
  orderLightboxVisible.value = true
}

function openDeliveryLightbox(index) {
  deliveryLightboxIndex.value = index
  deliveryLightboxVisible.value = true
}

// ── 发布人审核（人人可发单模式：交付由发单用户自己审核打款，管理员兜底）──
// 命名避开既有「订单评价」的 reviewForm/submitReview
const ownerClaims = computed(() => ordersStore.claims)
const pendingReviewCount = computed(() => ownerClaims.value.filter((claim) => claim.status === 'DELIVERED').length)
const showPayoutModal = ref(false)
const payoutForm = ref({ claimId: null, amount: '', deduction: '', note: '' })
const payoutSubmitting = ref(false)

function claimBoosterName(claim) {
  return claim?.booster_nickname || (claim?.booster_id != null ? `用户 #${claim.booster_id}` : '打手')
}

function openPayoutModal(claim) {
  payoutForm.value = {
    claimId: claim.id,
    amount: String(order.value?.price ?? ''),
    deduction: '',
    note: '',
  }
  showPayoutModal.value = true
}

async function submitPayout() {
  if (payoutSubmitting.value || payoutForm.value.claimId == null) {
    return
  }
  payoutSubmitting.value = true
  errorMessage.value = ''
  const payload = { action: 'approve' }
  const amount = Number(payoutForm.value.amount)
  if (payoutForm.value.amount !== '' && !Number.isNaN(amount)) {
    payload.amount = amount
  }
  const deduction = Number(payoutForm.value.deduction)
  if (order.value?.compensation_amount && payoutForm.value.deduction !== '' && !Number.isNaN(deduction)) {
    payload.deduction = deduction
  }
  if (payoutForm.value.note.trim()) {
    payload.note = payoutForm.value.note.trim()
  }
  const result = await ordersStore.reviewClaim(order.value.id, payoutForm.value.claimId, payload)
  payoutSubmitting.value = false
  if (result.success) {
    showPayoutModal.value = false
    successMessage.value = '已通过审核，报酬已入账对方钱包'
    await Promise.all([ordersStore.fetchOrder(order.value.id), ordersStore.fetchClaims(order.value.id)])
  } else {
    errorMessage.value = result.error || '审核失败，请稍后重试'
  }
}

// 报名名单：打手侧用于「已接单」按钮态；发布人侧用于审核面板（人人可发单模式自审）
async function loadClaims() {
  if (!order.value || !currentUser.value || (!isOwner.value && !isBooster.value)) {
    return
  }
  await ordersStore.fetchClaims(order.value.id)
}

function openClaimModal() {
  if (hasClaimed.value || actionLoading.value) {
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  showClaimModal.value = true
}

function closeClaimModal() {
  if (actionLoading.value) {
    return
  }
  showClaimModal.value = false
}

async function handleConfirmClaim() {
  actionLoading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const result = await ordersStore.acceptOrder(order.value.id)
  if (result.success) {
    showClaimModal.value = false
    successMessage.value = '已接手订单，开始进行吧'
    // 刷新订单状态与报名名单，让按钮切到「结束订单」/「已接手订单」态
    await ordersStore.fetchOrder(order.value.id)
    await loadClaims()
  } else {
    // 失败（含 409 重复确认）走页面既有错误提示
    showClaimModal.value = false
    errorMessage.value = result.error
  }
  actionLoading.value = false
}

function openDeliverModal() {
  errorMessage.value = ''
  successMessage.value = ''
  showDeliverModal.value = true
}

function onDeliverSuccess() {
  successMessage.value = '汇报已提交，等待订单发布人审核'
  // 刷新报名名单（不切换全局 loading，避免骨架屏闪断）；my_claim 随订单数据更新
  loadClaims()
}

async function handleConfirm() {
  actionLoading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const result = await ordersStore.confirmOrder(order.value.id)
  if (result.success) {
    successMessage.value = '已确认完成！'
    confirmSuccess.value = true
    window.setTimeout(() => { confirmSuccess.value = false }, 1500)
    await fetchReviews()
  } else {
    errorMessage.value = result.error
  }
  actionLoading.value = false
}

async function handleDispute() {
  const reason = window.prompt('请输入争议原因（可选）：')
  if (reason === null) {
    return
  }
  actionLoading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const result = await ordersStore.disputeOrder(order.value.id, reason)
  if (result.success) {
    successMessage.value = '已发起争议，平台将介入处理'
  } else {
    errorMessage.value = result.error
  }
  actionLoading.value = false
}

async function handleCancel() {
  if (!window.confirm('确定取消这条订单吗？')) {
    return
  }
  actionLoading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const result = await ordersStore.cancelOrder(order.value.id)
  if (result.success) {
    successMessage.value = '已取消'
  } else {
    errorMessage.value = result.error
  }
  actionLoading.value = false
}

async function handleStartConversation() {
  if (!chatTargetUserId.value) {
    return
  }

  chatLoading.value = true
  errorMessage.value = ''
  const result = await chatStore.startConversation(chatTargetUserId.value, order.value.id)
  if (result.success) {
    router.push({ name: 'chat-detail', params: { id: result.data.id } })
  } else {
    errorMessage.value = result.error
  }
  chatLoading.value = false
}

async function fetchReviews() {
  if (!order.value || order.value.status !== 'COMPLETED') {
    reviews.value = []
    return
  }

  try {
    const resp = await api.get(`/orders/${order.value.id}/reviews`)
    reviews.value = resp.data.items || []
  } catch {
    reviews.value = []
  }
}

function startEditReview(review) {
  reviewForm.value = { rating: review.rating, content: review.content || '' }
  editingReview.value = true
}

async function submitReview() {
  errorMessage.value = ''
  successMessage.value = ''
  const isEditing = editingReview.value

  try {
    if (isEditing) {
      await api.put(`/orders/${order.value.id}/reviews`, reviewForm.value)
    } else {
      await api.post(`/orders/${order.value.id}/reviews`, reviewForm.value)
    }
    editingReview.value = false
    reviewForm.value = { rating: 5, content: '' }
    successMessage.value = isEditing ? '评价更新了' : isAssignedBooster.value ? '评价已提交' : '谢谢，你的反馈会帮助更多人找到靠谱的代练'
    await fetchReviews()
  } catch (err) {
    errorMessage.value = err.message || '评价失败'
  }
}

onMounted(async () => {
  const result = await ordersStore.fetchOrder(props.id)
  if (result.success) {
    await fetchReviews()
    await loadClaims()
  }
})
</script>

<template>
  <div class="page-shell od-page space-y-6">
    <div v-if="loading" class="space-y-6" aria-busy="true">
      <div class="skeleton h-44 !rounded-panel"></div>
      <div class="grid gap-6 xl:grid-cols-[1.02fr_0.98fr]">
        <div class="skeleton h-72 !rounded-card"></div>
        <div class="skeleton h-72 !rounded-card"></div>
      </div>
    </div>

    <template v-else-if="order">
      <div v-if="errorMessage" class="message-error">{{ errorMessage }}</div>
      <div v-if="successMessage" class="message-success">{{ successMessage }}</div>

      <section class="hero-panel p-6 sm:p-8 lg:p-10" :style="heroStyle">
        <div class="relative z-10 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div class="min-w-0 space-y-3">
            <div class="flex flex-wrap items-center gap-3">
              <span class="tag">{{ order.game_name }}</span>
              <span :class="heroStatusClass">{{ heroStatusLabel }}</span>
            </div>
            <p v-if="humanStatusSubtitle" class="mt-1 text-sm text-ink-2">{{ humanStatusSubtitle }}</p>
            <h1 class="section-title break-words">{{ order.title || order.game_name || '代练订单' }}</h1>
            <p class="break-words text-sm text-ink-2">{{ order.intro || compactSummary() }}</p>
          </div>

          <!-- 统计卡：只显示已填写参数（未填不占位），移动端不再纵向堆叠占屏 -->
          <div class="grid grid-cols-2 gap-3 sm:gap-4">
            <article v-for="item in detailStats" :key="item.label" class="stat-card flex h-16 items-center gap-3 overflow-hidden sm:h-[4.5rem] sm:gap-4">
              <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-tile border border-line-1 bg-primary-soft text-base font-semibold text-primary sm:h-11 sm:w-11 sm:text-lg">{{ item.icon }}</div>
              <div class="min-w-0 flex-1"><p class="text-xs uppercase tracking-[0.12em] text-ink-3">{{ item.label }}</p><p class="mt-1.5 truncate text-sm font-medium tabular-nums text-ink-1 sm:mt-2" :class="item.valueClass">{{ item.value }}</p></div>
            </article>
          </div>
        </div>
      </section>

            <OrderTimeline :order="order" />

      <section class="surface-card od-key p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">关键信息</h2>
        <div class="od-key__grid mt-4">
          <div class="od-key__item od-key__item--price">
            <p class="info-tile__label">金额</p>
            <p class="od-key__price">{{ formatOrderPrice(order) }}</p>
          </div>
          <div v-if="order.server" class="od-key__item">
            <p class="info-tile__label">区服</p>
            <p class="info-tile__value break-words">{{ order.server }}</p>
          </div>
          <div v-if="order.deadline" class="od-key__item">
            <p class="info-tile__label">截止</p>
            <p class="od-key__deadline" :class="isDeadlineOverdue ? 'text-danger' : 'text-ink-1'">{{ deadlineRemaining }} &middot; {{ formatDateTime(order.deadline) }}</p>
            <p v-if="isDeadlineOverdue" class="mt-1 text-xs font-semibold text-danger">已截止，请尽快处理</p>
          </div>
          <div v-if="order.compensation_amount" class="od-key__item">
            <p class="info-tile__label">炸单赔偿金</p>
            <p class="info-tile__value tabular-nums text-warning">{{ formatPrice(order.compensation_amount) }}</p>
          </div>
          <div v-if="formatPayoutDelay(order)" class="od-key__item">
            <p class="info-tile__label">到账时效</p>
            <p class="info-tile__value">{{ formatPayoutDelay(order) }}</p>
          </div>
          <div class="od-key__item">
            <p class="info-tile__label">订单号</p>
            <p class="info-tile__value tabular-nums">#{{ order.id }}</p>
          </div>
        </div>

        <!-- 老板ID：接单后（my_claim 存在）才可见，打手据此加老板好友 -->
        <div class="mt-4 info-tile min-w-0" :class="myClaim ? '!border-primary/40' : ''">
          <p class="info-tile__label">老板ID</p>
          <div v-if="order.boss_contact" class="flex flex-wrap items-center gap-3">
            <p class="info-tile__value break-all font-semibold text-ink-1">{{ order.boss_contact }}</p>
            <button
              v-if="myClaim"
              type="button"
              class="btn-secondary shrink-0 !min-h-[32px] !px-3 !py-1 text-xs"
              @click="copyBossContact"
            >{{ bossContactCopied ? '已复制 ✓' : '复制' }}</button>
          </div>
          <p v-else-if="myClaim" class="info-tile__value text-ink-3">发布人未填写老板ID</p>
          <p v-else-if="isOwner" class="info-tile__value text-ink-3">未填写老板ID（打手接单后看不到）</p>
          <p v-else class="info-tile__value text-ink-3">接单后可见，用于添加老板好友</p>
          <p v-if="myClaim && order.boss_contact" class="mt-1 text-xs text-ink-3">接单后请尽快添加老板好友沟通进度</p>
        </div>

        <div v-if="order.description_raw || order.notes || order.user?.username || order.booster" class="mt-6 grid gap-4 sm:grid-cols-2">
          <div v-if="order.description_raw" class="info-tile min-w-0">
            <p class="info-tile__label">需求</p>
            <p class="info-tile__value break-words">{{ order.description_raw }}</p>
          </div>
          <div v-if="order.notes" class="info-tile min-w-0">
            <p class="info-tile__label">备注</p>
            <p class="info-tile__value break-words">{{ order.notes }}</p>
          </div>
          <div v-if="order.user?.username" class="info-tile min-w-0">
            <p class="info-tile__label">用户</p>
            <p class="info-tile__value break-words">{{ order.user.username }}</p>
          </div>
          <div v-if="order.booster" class="info-tile min-w-0">
            <p class="info-tile__label">打手</p>
            <p class="info-tile__value break-words">
              <router-link
                :to="{ name: 'booster-profile', params: { id: order.booster_id } }"
                class="text-primary underline-offset-4 hover:underline"
              >{{ order.booster.username }}</router-link>
            </p>
          </div>
        </div>
      </section>

      <!-- 发布人审核区：打手提交汇报后由发布人审核打款（管理员也可在派单台处理） -->
      <section v-if="isOwner && ownerClaims.length" class="surface-card p-6 sm:p-8">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-lg font-semibold text-ink-1">接单名单</h2>
          <span v-if="pendingReviewCount" class="rounded-full bg-warning/15 px-3 py-1 text-xs font-semibold text-warning">{{ pendingReviewCount }} 条待审核</span>
        </div>
        <p class="mt-1 text-xs text-ink-3">对方完成并提交汇报后，点「审核打款」确认入账</p>
        <div class="mt-4 space-y-3">
          <div v-for="claim in ownerClaims" :key="claim.id" class="info-tile flex flex-wrap items-center justify-between gap-3">
            <div class="min-w-0">
              <p class="truncate text-sm font-medium text-ink-1">
                {{ claimBoosterName(claim) }}
                <span v-if="claim.is_first" class="ml-1 text-xs font-semibold text-primary">首抢</span>
              </p>
              <p class="mt-0.5 text-xs text-ink-3">{{ formatDateTime(claim.created_at) }}</p>
              <p v-if="claim.status !== 'CLAIMED' && claim.delivery_note" class="mt-1 line-clamp-2 break-words text-xs leading-5 text-ink-2">汇报：{{ claim.delivery_note }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-3">
              <span :class="getClaimStatusMeta(claim.status).tagClass">{{ getClaimStatusMeta(claim.status).label }}</span>
              <button v-if="claim.status === 'DELIVERED'" type="button" class="btn-primary !min-h-[36px] !px-4" @click="openPayoutModal(claim)">审核打款</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 订单画廊：缩略图网格，点击进灯箱（管理/打手皆可查看） -->
      <section v-if="orderAttachments.length" class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">订单画廊</h2>
        <div class="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-5">
          <button
            v-for="(att, idx) in orderAttachments"
            :key="attachmentUrl(att) + idx"
            type="button"
            class="group relative overflow-hidden rounded-tile border border-line-1 bg-surface-2"
            @click="openOrderLightbox(idx)"
          >
            <img :src="attachmentUrl(att)" :alt="attachmentName(att) || '订单图片'" class="h-24 w-full object-cover transition group-hover:opacity-90" loading="lazy" />
          </button>
        </div>
      </section>

      <section v-if="isClaimDelivered && (deliveryNoteText || deliveryAttachments.length)" class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">结束汇报</h2>
        <p v-if="deliveryNoteText" class="mt-3 break-words text-sm leading-6 text-ink-2">{{ deliveryNoteText }}</p>
        <p v-else class="mt-3 text-sm text-ink-3">未填写文字汇报</p>
        <div v-if="deliveryAttachments.length" class="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-5">
          <button
            v-for="(att, idx) in deliveryAttachments"
            :key="att.url + idx"
            type="button"
            class="group relative overflow-hidden rounded-tile border border-line-1 bg-surface-2"
            @click="openDeliveryLightbox(idx)"
          >
            <img :src="att.url" :alt="att.name || '结束汇报截图'" class="h-24 w-full object-cover transition group-hover:opacity-90" loading="lazy" />
          </button>
        </div>
        <p v-if="myClaim?.status === 'DELIVERED' && !isOwner" class="message-info mt-4 text-xs leading-6">
          已提交结束汇报，等待订单发布人审核打款，通过后报酬会计入余额。
        </p>
        <p v-if="isDelivered && isOwner" class="message-warning mt-4 text-xs leading-6">
          打手已结束订单，请核实汇报与结果。如有问题可发起争议。
        </p>
      </section>

      <div class="grid gap-6 xl:grid-cols-[1.02fr_0.98fr] xl:items-start">
        <section class="space-y-6">
          <article class="surface-card p-6 sm:p-8">
            <h2 class="text-lg font-semibold text-ink-1">说明</h2>
            <p v-if="isLocked && isBoostOrder && isOwner" class="message-warning mt-4 text-xs leading-6">
              打手正在进行你的订单，请耐心等待，有疑问可随时联系打手。
            </p>
            <p class="mt-4 text-sm leading-6 text-ink-2">如有疑问可联系对方沟通，或对进行中/待确认订单发起争议。</p>
            <button
              v-if="canStartChat"
              class="btn-secondary mt-4 w-full py-2.5"
              :disabled="chatLoading"
              @click="handleStartConversation"
            >
              {{ chatLoading ? '打开中...' : (isOwner ? '联系打手' : '联系老板') }}
            </button>
          </article>
        </section>

        <aside class="od-ops surface-card p-6 sm:p-8 xl:sticky xl:top-28">
          <div class="od-ops__head flex items-center justify-between gap-4">
            <h2 class="text-lg font-semibold text-ink-1">操作</h2>
            <span class="chat-status-pill shrink-0">{{ statusMeta.label }}</span>
          </div>

          <!-- 桌面竖排；<xl 压缩为单行操作栏：返回列表 | 联系 | 主操作 -->
          <div class="od-ops__body mt-6 flex flex-col gap-3">
            <button
              v-if="isBooster && order.status === 'PENDING' && !isOwner"
              class="od-ops__primary btn-primary w-full py-3"
              :disabled="actionLoading || hasClaimed"
              @click="openClaimModal"
            >
              {{ hasClaimed ? '已接手订单' : '接手订单' }}
            </button>

            <button
              v-if="isBooster && order.status === 'LOCKED' && hasClaimed && !isAssignedBooster && !myClaim"
              type="button"
              class="od-ops__chip btn-secondary w-full py-3"
              disabled
            >
              已接手订单 · 排队中
            </button>

            <button
              v-if="canDeliver"
              class="od-ops__primary btn-success w-full py-3"
              :disabled="actionLoading"
              @click="openDeliverModal"
            >
              结束订单
            </button>

            <button
              v-if="myClaim?.status === 'DELIVERED'"
              type="button"
              class="od-ops__chip btn-secondary w-full py-3"
              disabled
            >
              已提交汇报 · 待审核
            </button>

            <button
              v-if="!myClaim && isAssignedBooster && order.status === 'DELIVERED'"
              type="button"
              class="od-ops__chip btn-secondary w-full py-3"
              disabled
            >
              已结束 · 待老板确认
            </button>

            <button
              v-if="isOwner && order.status === 'DELIVERED'"
              class="od-ops__primary btn-success w-full py-3"
              :class="{ 'btn-confirm-success': confirmSuccess }"
              :disabled="actionLoading"
              @click="handleConfirm"
            >
              确认完成
            </button>

            <button
              v-if="(isOwner || isAssignedBooster) && ['LOCKED', 'DELIVERED'].includes(order.status)"
              class="od-ops__desktop-only btn-danger w-full py-3"
              :disabled="actionLoading"
              @click="handleDispute"
            >
              发起争议
            </button>

            <button
              v-if="isOwner && order.status === 'PENDING'"
              class="od-ops__primary btn-danger w-full py-3"
              :disabled="actionLoading"
              @click="handleCancel"
            >
              取消订单
            </button>

            <!-- 争议状态：管理员跳转派单台，争议双方联系管理员 -->
            <template v-if="order.status === 'DISPUTED'">
              <p v-if="isAdmin" class="message-warning text-xs leading-6">
                该订单有争议，请前往派单台处理。
              </p>
              <button
                v-if="isAdmin"
                type="button"
                class="od-ops__primary btn-primary w-full py-3"
                @click="router.push({ name: 'admin-dispatch-detail', params: { id: order.id } })"
              >
                前往派单台处理
              </button>
              <button
                v-else-if="isOwner || isAssignedBooster"
                type="button"
                class="btn-secondary w-full py-3"
                @click="router.push({ name: 'support' })"
              >
                联系管理员
              </button>
            </template>

            <button class="od-ops__back btn-secondary w-full py-3" @click="router.push({ name: 'orders' })">返回列表</button>
          </div>
        </aside>
      </div>

      <section v-if="order.status === 'COMPLETED'" class="surface-card space-y-4 p-6 sm:p-8">
        <h3 class="section-title !text-2xl">{{ isAssignedBooster ? '给老板留个评价' : '说说这次体验' }}</h3>

        <div v-for="review in reviews" :key="review.id" class="stat-card">
          <div class="flex items-center justify-between gap-3">
            <span class="text-sm text-ink-2">{{ review.reviewer?.username }}</span>
            <span class="text-warning">{{ '★'.repeat(review.rating) }}{{ '☆'.repeat(5 - review.rating) }}</span>
          </div>
          <p v-if="review.content" class="mt-2 text-sm text-ink-2">{{ review.content }}</p>
          <button
            v-if="review.reviewer_id === currentUser?.id"
            type="button"
            class="btn-ghost mt-2 !px-3 !py-1 !text-xs"
            @click="startEditReview(review)"
          >
            修改
          </button>
        </div>

        <div v-if="(canReview && !hasReviewed) || editingReview" class="stat-card space-y-3">
          <p class="info-tile__label">
            {{ editingReview ? '修改评价' : '写评价' }}
          </p>
          <div class="flex gap-1">
            <button
              v-for="star in 5"
              :key="star"
              type="button"
              class="text-2xl transition-transform duration-150 hover:scale-110"
              :class="star <= reviewForm.rating ? 'text-warning' : 'text-ink-3'"
              @click="reviewForm.rating = star"
            >
              ★
            </button>
          </div>
          <textarea
            v-model="reviewForm.content"
            class="input resize-none"
            :placeholder="isAssignedBooster ? '老板好配合吗？沟通顺畅吗？' : '打法怎么样？服务态度好不好？达到你的预期了吗？'"
          ></textarea>
          <div class="flex gap-2">
            <button type="button" class="btn-primary !px-4 !py-2" @click="submitReview">
              {{ editingReview ? '保存修改' : '说说这次体验' }}
            </button>
            <button v-if="editingReview" type="button" class="btn-ghost !px-4 !py-2" @click="editingReview = false">
              取消
            </button>
          </div>
        </div>
      </section>
    </template>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🔍</div>
      <h2 class="empty-state__title">订单不存在</h2>
      <p class="empty-state__copy">这条订单可能已被删除，或没有访问权限。</p>
    </section>

    <!-- 弹窗/灯箱挂在页面根节点：loading 骨架屏切换会卸载子组件并丢弃其关闭事件 -->
    <template v-if="order">
      <OrderDeliverModal v-model="showDeliverModal" :order-id="order.id" @success="onDeliverSuccess" />

      <!-- 两步确认：详情页先弹「接手订单」，确认后订单进入进行中 -->
      <teleport to="body">
        <div v-if="showClaimModal" class="modal-scrim" @click.self="closeClaimModal">
          <div class="modal-card" role="dialog" aria-modal="true" aria-label="接手订单">
            <h3 class="text-lg font-semibold text-ink-1">接手订单</h3>
            <p class="mt-3 text-sm leading-6 text-ink-2">
              即将接手订单「{{ claimSubject }}」，报酬
              <span class="font-semibold tabular-nums text-price">{{ formatOrderPrice(order) }}</span>
              ，接手后开始进行，完成后点击「结束订单」提交汇报。
            </p>
            <div class="mt-6 flex gap-3">
              <button type="button" class="btn-secondary flex-1" :disabled="actionLoading" @click="closeClaimModal">取消</button>
              <button type="button" class="btn-primary flex-1" :disabled="actionLoading" @click="handleConfirmClaim">
                {{ actionLoading ? '接手中…' : '接手订单' }}
              </button>
            </div>
          </div>
        </div>
      </teleport>

      <!-- 发布人审核打款弹窗（人人可发单模式：交付由发单用户自审） -->
      <teleport to="body">
        <div v-if="showPayoutModal" class="modal-scrim" @click.self="!payoutSubmitting && (showPayoutModal = false)">
          <div class="modal-card" role="dialog" aria-modal="true" aria-label="审核打款">
            <h3 class="text-lg font-semibold text-ink-1">审核打款</h3>
            <p class="mt-2 text-sm leading-6 text-ink-2">
              通过后按填写金额从托管余额打款给对方；订单报酬
              <span class="font-semibold tabular-nums text-price">{{ formatOrderPrice(order) }}</span>
            </p>
            <div class="mt-4 space-y-4">
              <div>
                <label class="label" for="payout-amount">打款金额</label>
                <input id="payout-amount" v-model="payoutForm.amount" type="number" min="0" step="0.01" class="input" placeholder="默认订单全额" />
              </div>
              <div v-if="order.compensation_amount">
                <label class="label" for="payout-deduction">扣除炸单赔偿金（0 ~ {{ formatPrice(order.compensation_amount) }}）</label>
                <input id="payout-deduction" v-model="payoutForm.deduction" type="number" min="0" :max="Number(order.compensation_amount)" step="0.01" class="input" placeholder="打炸了才扣，默认 0" />
              </div>
              <div>
                <label class="label" for="payout-note">备注（可选）</label>
                <textarea id="payout-note" v-model="payoutForm.note" rows="2" class="input" placeholder="随钱包流水留存"></textarea>
              </div>
            </div>
            <div class="mt-6 flex gap-3">
              <button type="button" class="btn-secondary flex-1" :disabled="payoutSubmitting" @click="showPayoutModal = false">取消</button>
              <button type="button" class="btn-primary flex-1" :disabled="payoutSubmitting" @click="submitPayout">
                {{ payoutSubmitting ? '提交中…' : '通过并打款' }}
              </button>
            </div>
          </div>
        </div>
      </teleport>

      <Lightbox :images="orderAttachments" :visible="orderLightboxVisible" :start-index="orderLightboxIndex" @close="orderLightboxVisible = false" />
      <Lightbox :images="deliveryAttachments" :visible="deliveryLightboxVisible" :start-index="deliveryLightboxIndex" @close="deliveryLightboxVisible = false" />
    </template>
  </div>
</template>
