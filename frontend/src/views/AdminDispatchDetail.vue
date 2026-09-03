<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import Lightbox from '@/components/Lightbox.vue'
import { useOrdersStore } from '@/stores/orders'
import api from '@/utils/api'
import { formatDateTime, formatOrderPrice, formatPrice } from '@/utils/display'
import { getClaimStatusMeta, getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'

const props = defineProps({
  id: {
    type: [String, Number],
    required: true,
  },
})

const router = useRouter()
const ordersStore = useOrdersStore()

const order = ref(null)
const loading = ref(true)
const error = ref('')
const message = ref({ type: '', text: '' })
const actionKey = ref('')

// 子导航：订单详情 / 接单名单 / 审核列表
const detailTab = ref('info')

const claims = computed(() => ordersStore.claims)
const claimsLoading = computed(() => ordersStore.claimsLoading)

// ── 展示辅助 ──

function normalizeAttachments(value) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (typeof item === 'string' ? { url: item, name: '' } : { url: item?.url || '', name: item?.name || '' }))
    .filter((item) => item.url)
}

function formatApiError(value) {
  if (Array.isArray(value)) {
    return value.map((item, index) => `${index + 1}. ${item?.msg || JSON.stringify(item)}`).join('；')
  }
  return value
}

function statusLabel(o) {
  return o.status === 'DELIVERED' ? '待审核' : getOrderStatusLabel(o.status)
}

function priceLabel(o) {
  return formatOrderPrice(o)
}

function claimStatusLabel(o) {
  const map = { OPEN: '接单中', PAUSED: '已暂停', FULL: '已满员', CLOSED: '已截止' }
  return map[o.claim_status] || o.claim_status || '-'
}

// claim 状态：CLAIMED=进行中 / DELIVERED=待审核 / SETTLED=已结算（共享语义色）
function claimStatusMeta(claim) {
  return getClaimStatusMeta(claim?.status)
}

function claimBoosterName(claim) {
  return claim?.booster_nickname || (claim?.booster_id != null ? `打手 #${claim.booster_id}` : '打手')
}

const orderAttachments = computed(() => normalizeAttachments(order.value?.attachments))

// ── 灯箱：订单图片 / 汇报图片共用 ──

const lightbox = ref({ visible: false, images: [], index: 0 })

function openLightbox(images, index) {
  lightbox.value = { visible: true, images, index }
}

// ── 编辑订单（PENDING 可改：PUT /orders/{id} + 附件增删）──

const attachmentTypes = ['image/png', 'image/jpeg', 'image/webp']
const maxAttachmentCount = 5
const maxAttachmentSize = 10 * 1024 * 1024

const editPanel = ref(null)

function toDatetimeLocalValue(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function applyEditData(state, o) {
  state.title = o.title || ''
  state.intro = o.intro || ''
  state.description = o.description_raw || o.description || ''
  state.price = o.price != null ? String(o.price) : ''
  state.max_claims = o.max_claims ?? 1
  state.deadline = toDatetimeLocalValue(o.deadline)
  state.attachments = normalizeAttachments(o.attachments)
  // 增量契约字段：老板ID / 炸单赔偿金（开关+金额）/ 到账时效
  state.boss_contact = o.boss_contact || ''
  state.compensation_enabled = Number(o.compensation_amount ?? 0) > 0
  state.compensation_amount = o.compensation_amount != null ? String(o.compensation_amount) : ''
  state.payout_delay_days = o.payout_delay_days != null ? String(o.payout_delay_days) : ''
}

// 到账时效选项：不设置（默认）/ 1-5 天
const payoutDelayOptions = [
  { value: '', label: '不设置' },
  { value: '1', label: '1天' },
  { value: '2', label: '2天' },
  { value: '3', label: '3天' },
  { value: '4', label: '4天' },
  { value: '5', label: '5天' },
]

function openEditPanel() {
  const o = order.value
  if (!o) return
  editPanel.value = {
    title: '', intro: '', description: '', price: '',
    max_claims: 1, deadline: '', attachments: [], newFiles: null,
    boss_contact: '', compensation_enabled: false, compensation_amount: '', payout_delay_days: '',
    error: '', submitting: false, uploading: '', uploadProgress: '', removingIndex: -1,
  }
  applyEditData(editPanel.value, o)
}

function closeEditPanel() {
  editPanel.value = null
}

function validateEditAttachments(state) {
  const selected = Array.from(state.newFiles || [])
  if (!selected.length) return ''
  if (state.attachments.length + selected.length > maxAttachmentCount) {
    return `订单最多保留 ${maxAttachmentCount} 张图片（已有 ${state.attachments.length} 张）`
  }
  const invalid = selected.find((file) => !attachmentTypes.includes((file.type || '').toLowerCase()))
  if (invalid) return `仅支持 PNG、JPEG、WebP 图片：${invalid.name}`
  const oversized = selected.find((file) => file.size > maxAttachmentSize)
  if (oversized) return `单张图片不能超过10MB：${oversized.name}`
  return ''
}

async function removeEditAttachment(index) {
  const state = editPanel.value
  if (!state || state.submitting || state.removingIndex !== -1) return
  state.error = ''
  state.removingIndex = index
  try {
    const response = await api.delete(`/orders/${order.value.id}/attachments/${index}`)
    state.attachments = normalizeAttachments(response.data?.attachments)
    if (order.value) order.value = { ...order.value, attachments: response.data?.attachments ?? order.value.attachments }
    message.value = { type: 'success', text: '图片已删除' }
  } catch (err) {
    state.error = formatApiError(err.message) || '删除图片失败'
  } finally {
    if (editPanel.value) state.removingIndex = -1
  }
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
    } catch (err) {
      throw new Error(`第 ${index + 1} 张图片上传失败：${err.message || '请稍后重试'}`)
    }
  }
  state.uploading = ''
  state.uploadProgress = ''
}

async function submitEdit() {
  const state = editPanel.value
  if (!state || state.submitting) return
  state.error = ''

  const attachmentError = validateEditAttachments(state)
  if (attachmentError) {
    state.error = attachmentError
    return
  }
  // 价格校验：只保留单一发单价（区间价格已下线）
  const price = Number(state.price)
  if (!Number.isFinite(price) || price <= 0) {
    state.error = '请填写大于 0 的价格'
    return
  }
  const maxClaims = Number(state.max_claims)
  if (!Number.isInteger(maxClaims) || maxClaims < 1 || maxClaims > 100) {
    state.error = '最大接单人数需为 1-100 的整数'
    return
  }

  // 老板ID ≤64 字；赔偿金开启后必须 >0；到账时效 1-5 天或不设置
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
  const result = await ordersStore.editOrder(order.value.id, {
    title: state.title.trim() || null,
    intro: state.intro.trim() || null,
    description: state.description.trim() || null,
    price,
    // 显式清空历史区间残留，避免后端保留旧的 price_min/price_max
    price_min: null,
    price_max: null,
    max_claims: maxClaims,
    deadline: state.deadline || null,
    boss_contact: bossContact || null,
    compensation_amount: compensationAmount,
    payout_delay_days: payoutDelay,
  })
  if (!result.success) {
    state.submitting = false
    state.error = formatApiError(result.error) || '保存失败'
    return
  }

  if (state.newFiles && state.newFiles.length) {
    try {
      await uploadOrderAttachments(order.value.id, state.newFiles, state)
    } catch (err) {
      state.submitting = false
      state.uploading = ''
      state.error = `订单信息已保存，但${err.message}`
      await fetchOrder()
      return
    }
  }

  state.submitting = false
  message.value = { type: 'success', text: `订单 #${order.value.id} 已更新` }
  closeEditPanel()
  await fetchOrder()
}

// ── 审核列表 + 审核弹窗（claims 维度：DELIVERED=待审核 / SETTLED=已结算）──

// 待审核：已交付汇报的 claim；已通过：已结算的 claim
const pendingReviewClaims = computed(() => claims.value.filter((claim) => claim.status === 'DELIVERED'))
const settledClaims = computed(() => claims.value.filter((claim) => claim.status === 'SETTLED'))
const pendingReviewCount = computed(() => pendingReviewClaims.value.length)
const approvedCount = computed(() => settledClaims.value.length)

function claimAttachments(claim) {
  return normalizeAttachments(claim?.delivery_attachments)
}

// 审核弹窗：查看汇报 + 打款（全额 / 部分金额 + 备注 + 扣除炸单赔偿金）
const reviewModal = ref(null) // { claim, payout: { mode, amount, note, deduction }, submitting }

function openReviewModal(claim) {
  reviewModal.value = { claim, payout: { mode: 'full', amount: '', note: '', deduction: '0' }, submitting: false }
}

function closeReviewModal() {
  if (!reviewModal.value?.submitting) reviewModal.value = null
}

// 审核打款上限 = max(order.price, order.price_max)（兼容历史区间单）
function payoutCeiling() {
  return Math.max(Number(order.value?.price ?? 0), Number(order.value?.price_max ?? 0))
}

// 该单炸单赔偿金（>0 才展示扣除输入）
const orderCompensationAmount = computed(() => Number(order.value?.compensation_amount ?? 0))

function payoutAmountError() {
  const payout = reviewModal.value?.payout
  if (!payout || payout.mode !== 'partial') return ''
  const amount = Number(payout.amount)
  if (!Number.isFinite(amount) || amount <= 0) return '请填写大于 0 的到账金额'
  const ceiling = payoutCeiling()
  if (ceiling && amount > ceiling) return `到账金额不能超过订单报酬 ${formatPrice(ceiling)}`
  return ''
}

function payoutDeductionError() {
  if (!orderCompensationAmount.value || reviewModal.value?.claim?.status !== 'DELIVERED') return ''
  const deduction = Number(reviewModal.value?.payout?.deduction)
  if (!Number.isFinite(deduction) || deduction < 0) return '扣除的赔偿金不能小于 0'
  if (deduction > orderCompensationAmount.value) {
    return `扣除的赔偿金不能超过该单赔偿金 ${formatPrice(orderCompensationAmount.value)}`
  }
  return ''
}

async function submitPayout() {
  if (!reviewModal.value || reviewModal.value.submitting) return
  const payout = reviewModal.value.payout
  const claim = reviewModal.value.claim
  const amountError = payoutAmountError()
  if (amountError) {
    message.value = { type: 'error', text: amountError }
    return
  }
  const deductionError = payoutDeductionError()
  if (deductionError) {
    message.value = { type: 'error', text: deductionError }
    return
  }
  const payload = { action: 'approve' }
  if (payout.mode === 'partial') payload.amount = Number(payout.amount)
  const note = payout.note.trim()
  if (note) payload.note = note
  // 炸单赔偿金扣除：0 ≤ deduction ≤ compensation_amount（仅该单设置了赔偿金时提交）
  const deduction = orderCompensationAmount.value > 0 ? Number(payout.deduction) || 0 : 0
  if (orderCompensationAmount.value > 0) {
    payload.deduction = deduction
  }

  reviewModal.value.submitting = true
  const result = await ordersStore.reviewClaim(order.value.id, claim.id, payload)
  reviewModal.value.submitting = false
  if (result.success) {
    const gross = payout.mode === 'partial' ? Number(payout.amount) : Number(order.value?.price ?? 0)
    const net = Math.max(0, gross - deduction)
    let text
    if (deduction > 0) {
      text = `已入账 ${formatPrice(gross)}，炸单赔偿金扣除 ${formatPrice(deduction)}、返还 ${formatPrice(Math.max(0, orderCompensationAmount.value - deduction))}，单 #${claim.id} 已结算`
    } else {
      text = payout.mode === 'partial'
        ? `已按 ${formatPrice(Number(payout.amount))} 部分到账，单 #${claim.id} 已结算`
        : `审核通过，单 #${claim.id} 报酬已计入打手余额`
    }
    message.value = { type: 'success', text }
    closeReviewModal()
    await Promise.all([fetchOrder(), ordersStore.fetchClaims(order.value.id)])
  } else {
    message.value = { type: 'error', text: formatApiError(result.error) || '审核失败' }
  }
}

// ── 管理操作：暂停/恢复/截止/归档/删除/退款 ──

async function controlOrder(action) {
  actionKey.value = `control-${action}`
  try {
    const response = await api.put(`/orders/${order.value.id}/claim-control`, { action })
    order.value = response.data
    message.value = { type: 'success', text: '订单控制已更新' }
  } catch (err) {
    message.value = { type: 'error', text: err.message || '操作失败' }
  } finally {
    actionKey.value = ''
  }
}

async function refundOrder() {
  actionKey.value = 'refund'
  try {
    const response = await api.put(`/orders/${order.value.id}/refund`)
    order.value = response.data
    message.value = { type: 'success', text: '退款成功' }
  } catch (err) {
    message.value = { type: 'error', text: err.message || '退款失败' }
  } finally {
    actionKey.value = ''
  }
}

// ── 争议处理：PUT /admin/orders/{id}/intervene（action 取自后端 allowed_actions） ──

async function interveneOrder(action) {
  const label = action === 'COMPLETED' ? '完结并结算给打手' : '取消订单'
  const hint = action === 'COMPLETED'
    ? '订单将变为 COMPLETED 并按现有逻辑结算给打手。'
    : '订单将变为 CANCELLED；如已支付，需再点「退款」退回款项。'
  if (!window.confirm(`确定对订单 #${order.value.id} 执行「${label}」吗？${hint}`)) return
  actionKey.value = `intervene-${action}`
  try {
    const response = await api.put(`/admin/orders/${order.value.id}/intervene`, { action })
    order.value = response.data
    message.value = { type: 'success', text: `已${label}` }
  } catch (err) {
    message.value = { type: 'error', text: err.message || '处理失败' }
  } finally {
    actionKey.value = ''
  }
}

async function deleteThisOrder() {
  if (!window.confirm(`确定删除订单 #${order.value.id} 吗？此操作不可恢复。`)) return
  actionKey.value = 'delete'
  try {
    await api.delete(`/orders/${order.value.id}`)
    message.value = { type: 'success', text: '订单已删除' }
    router.push({ path: '/admin', query: { tab: 'orders' } })
  } catch (err) {
    message.value = { type: 'error', text: err.message || '删除失败' }
  } finally {
    actionKey.value = ''
  }
}

// ── 加载 ──

async function fetchOrder() {
  const result = await ordersStore.fetchOrder(props.id)
  if (result.success) {
    order.value = result.data
    error.value = ''
  } else {
    error.value = formatApiError(result.error) || '订单加载失败'
  }
}

onMounted(async () => {
  // 并行拉取：订单详情与接单名单互不依赖（远程库延迟下串行会翻倍）
  await Promise.all([fetchOrder(), ordersStore.fetchClaims(props.id)])
  loading.value = false
})
</script>

<template>
  <div class="page-shell space-y-6">
    <!-- 顶栏：返回 + 标题 + 状态 -->
    <div class="flex flex-wrap items-center gap-3">
      <button type="button" class="btn-ghost !px-3 !py-2" @click="router.push({ path: '/admin', query: { tab: 'orders' } })">
        ← 派单列表
      </button>
      <span v-if="order" :class="getOrderStatusBadgeClass(order.status)">{{ statusLabel(order) }}</span>
    </div>

    <div v-if="error" class="message-error">{{ error }}</div>
    <div v-if="message.text" :class="message.type === 'success' ? 'message-success' : message.type === 'error' ? 'message-error' : 'message-info'">{{ message.text }}</div>

    <div v-if="loading" class="space-y-4" aria-busy="true">
      <div class="skeleton h-24 !rounded-card"></div>
      <div class="skeleton h-40 !rounded-card"></div>
      <div class="skeleton h-40 !rounded-card"></div>
    </div>

    <template v-else-if="order">
      <!-- 订单信息（订单详情 tab） -->
      <section v-if="detailTab === 'info'" class="surface-card p-6 sm:p-8">
        <h1 class="text-xl font-semibold text-ink-1">{{ order.title || `${order.game_name} 订单` }}</h1>
        <p class="mt-1 text-sm text-ink-3">
          #{{ order.id }} · {{ order.game_name }}
        </p>
        <p v-if="order.intro" class="mt-3 break-words text-sm leading-6 text-ink-2">{{ order.intro }}</p>

        <div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div class="info-tile">
            <p class="info-tile__label">报酬</p>
            <p class="info-tile__value text-base font-semibold tabular-nums text-price">{{ priceLabel(order) }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">名额</p>
            <p class="info-tile__value tabular-nums">{{ Number(order.claimed_count ?? 0) }}/{{ Number(order.max_claims ?? 0) }} · {{ claimStatusLabel(order) }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">截止</p>
            <p class="info-tile__value">{{ order.deadline ? formatDateTime(order.deadline) : '未设置' }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">发布时间</p>
            <p class="info-tile__value">{{ formatDateTime(order.created_at) }}</p>
          </div>
        </div>

        <!-- 增量契约字段：老板ID / 炸单赔偿金 / 到账时效（有值才展示） -->
        <div v-if="order.boss_contact || Number(order.compensation_amount) > 0 || order.payout_delay_days" class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div v-if="order.boss_contact" class="info-tile min-w-0">
            <p class="info-tile__label">老板ID</p>
            <p class="info-tile__value break-words">{{ order.boss_contact }}</p>
          </div>
          <div v-if="Number(order.compensation_amount) > 0" class="info-tile">
            <p class="info-tile__label">炸单赔偿金</p>
            <p class="info-tile__value tabular-nums">{{ formatPrice(order.compensation_amount) }}</p>
          </div>
          <div v-if="order.payout_delay_days" class="info-tile">
            <p class="info-tile__label">到账时效</p>
            <p class="info-tile__value tabular-nums">{{ order.payout_delay_days }}天到账</p>
          </div>
        </div>

        <div v-if="order.description_raw || order.notes" class="mt-4 grid gap-3 sm:grid-cols-2">
          <div v-if="order.description_raw" class="info-tile min-w-0">
            <p class="info-tile__label">需求</p>
            <p class="info-tile__value break-words">{{ order.description_raw }}</p>
          </div>
          <div v-if="order.notes" class="info-tile min-w-0">
            <p class="info-tile__label">备注</p>
            <p class="info-tile__value break-words">{{ order.notes }}</p>
          </div>
        </div>

        <!-- 订单图片 -->
        <div v-if="orderAttachments.length" class="mt-5">
          <p class="info-tile__label">订单图片（点击放大）</p>
          <div class="mt-2 grid grid-cols-3 gap-3 sm:grid-cols-5">
            <button
              v-for="(image, index) in orderAttachments"
              :key="image.url + index"
              type="button"
              class="group relative overflow-hidden rounded-tile border border-line-1 bg-surface-2"
              @click="openLightbox(orderAttachments, index)"
            >
              <img :src="image.url" :alt="image.name || '订单图片'" class="h-24 w-full object-cover transition group-hover:opacity-90" loading="lazy" />
            </button>
          </div>
        </div>
      </section>

      <!-- 子导航：订单详情 / 接单名单 / 审核列表 -->
      <nav class="tab-bar" aria-label="派单处理视图">
        <button type="button" :class="detailTab === 'info' ? 'tab-pill-active' : 'tab-pill'" @click="detailTab = 'info'">订单详情</button>
        <button type="button" :class="detailTab === 'claims' ? 'tab-pill-active' : 'tab-pill'" @click="detailTab = 'claims'">接单名单</button>
        <button type="button" :class="detailTab === 'review' ? 'tab-pill-active' : 'tab-pill'" @click="detailTab = 'review'">
          审核列表<span v-if="pendingReviewCount" class="ml-1 rounded-full bg-warning px-1.5 text-xs text-white">{{ pendingReviewCount }}</span>
        </button>
      </nav>

      <!-- 编辑订单（订单详情 tab，PENDING） -->
      <section v-if="detailTab === 'info' && order.status === 'PENDING' && !editPanel" class="surface-card p-6 sm:p-8">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold text-ink-1">编辑相关信息</h2>
          <button type="button" class="btn-secondary !px-5 !py-2" @click="openEditPanel">编辑订单</button>
        </div>
        <p class="mt-2 text-sm text-ink-3">订单被接手前可修改标题、简介、报酬、名额与截止时间，也可补充或删除图片。</p>
      </section>

      <section v-if="detailTab === 'info' && editPanel" class="surface-card p-6 sm:p-8">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold text-ink-1">编辑订单</h2>
          <button type="button" class="btn-ghost !px-4 !py-2" @click="closeEditPanel">收起</button>
        </div>

        <form class="mt-5 space-y-4" @submit.prevent="submitEdit">
          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="label" for="edit-title">标题</label>
              <input id="edit-title" v-model="editPanel.title" type="text" class="input" placeholder="订单标题" :disabled="editPanel.submitting" />
            </div>
            <div>
              <label class="label" for="edit-max-claims">最大接单人数</label>
              <input id="edit-max-claims" v-model="editPanel.max_claims" type="number" min="1" max="100" class="input" :disabled="editPanel.submitting" />
            </div>
          </div>
          <div>
            <label class="label" for="edit-intro">简介</label>
            <input id="edit-intro" v-model="editPanel.intro" type="text" class="input" placeholder="一句话说明" :disabled="editPanel.submitting" />
          </div>
          <div>
            <label class="label" for="edit-description">需求描述</label>
            <textarea id="edit-description" v-model="editPanel.description" class="input resize-none" rows="3" :disabled="editPanel.submitting"></textarea>
          </div>
          <!-- 价格：只保留一个发单价格输入（区间价格已下线） -->
          <div>
            <label class="label" for="edit-price">发单价格</label>
            <input
              id="edit-price"
              v-model="editPanel.price"
              type="number"
              min="0.01"
              step="0.01"
              class="input"
              placeholder="例如 60"
              :disabled="editPanel.submitting"
            />
          </div>

          <!-- 增量契约字段：老板ID / 炸单赔偿金 / 到账时效 -->
          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="label" for="edit-boss-contact">老板ID（可选）</label>
              <input
                id="edit-boss-contact"
                v-model="editPanel.boss_contact"
                type="text"
                class="input"
                maxlength="64"
                placeholder="接单后打手可见，用于添加你为好友"
                :disabled="editPanel.submitting"
              />
            </div>
            <div>
              <label class="label" for="edit-payout-delay">到账时效</label>
              <select id="edit-payout-delay" v-model="editPanel.payout_delay_days" class="input" :disabled="editPanel.submitting">
                <option v-for="option in payoutDelayOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </div>
          </div>
          <!-- 炸单赔偿金：开关默认关，开启后填写金额（>0） -->
          <div class="rounded-tile border border-line-1 p-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p class="text-sm font-semibold text-ink-1">炸单赔偿金</p>
                <p class="mt-1 text-xs leading-5 text-ink-3">打手接单时冻结，订单完结自动返还；炸单可由管理员扣除。</p>
              </div>
              <button
                type="button"
                :class="editPanel.compensation_enabled ? 'filter-pill-active' : 'filter-pill'"
                :aria-pressed="editPanel.compensation_enabled"
                :disabled="editPanel.submitting"
                @click="editPanel.compensation_enabled = !editPanel.compensation_enabled"
              >
                {{ editPanel.compensation_enabled ? '已开启' : '未开启' }}
              </button>
            </div>
            <div v-if="editPanel.compensation_enabled" class="mt-3">
              <label class="label" for="edit-compensation">赔偿金额</label>
              <input id="edit-compensation" v-model="editPanel.compensation_amount" type="number" min="0.01" step="0.01" class="input" placeholder="例如 50" :disabled="editPanel.submitting" />
            </div>
          </div>
          <div>
            <label class="label" for="edit-deadline">截止时间</label>
            <input id="edit-deadline" v-model="editPanel.deadline" type="datetime-local" class="input" :disabled="editPanel.submitting" />
          </div>

          <!-- 已有图片：可删除 -->
          <div v-if="editPanel.attachments.length">
            <p class="label">已有图片</p>
            <div class="mt-2 flex flex-wrap gap-2.5">
              <div v-for="(image, index) in editPanel.attachments" :key="image.url + index" class="relative">
                <img :src="image.url" :alt="image.name || '订单图片'" class="h-20 w-20 rounded-tile border border-line-1 object-cover" />
                <button
                  type="button"
                  class="absolute -right-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full bg-danger text-xs font-bold text-white"
                  :aria-label="`删除图片 ${index + 1}`"
                  :disabled="editPanel.removingIndex !== -1 || editPanel.submitting"
                  @click="removeEditAttachment(index)"
                >
                  ×
                </button>
              </div>
            </div>
          </div>

          <div>
            <label class="label" for="edit-new-files">补充图片（保存时上传，最多 5 张）</label>
            <input
              id="edit-new-files"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              class="input"
              :disabled="editPanel.submitting"
              @change="editPanel.newFiles = $event.target.files"
            />
            <p v-if="editPanel.newFiles && editPanel.newFiles.length" class="mt-1 text-xs text-ink-2">
              已选择 {{ editPanel.newFiles.length }} 张新图片，保存时上传。
            </p>
            <p v-if="editPanel.uploadProgress" class="mt-2 text-sm text-primary">图片上传中：{{ editPanel.uploadProgress }}</p>
          </div>

          <div v-if="editPanel.error" class="message-error">{{ editPanel.error }}</div>

          <div class="flex gap-3">
            <button type="submit" class="btn-primary !px-5 !py-2.5" :disabled="editPanel.submitting">
              {{ editPanel.submitting ? '保存中...' : '保存修改' }}
            </button>
            <button type="button" class="btn-secondary !px-5 !py-2.5" :disabled="editPanel.submitting" @click="closeEditPanel">取消</button>
          </div>
        </form>
      </section>

      <!-- 接单名单 tab -->
      <section v-if="detailTab === 'claims'" class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">接单名单</h2>
        <p class="mt-1 text-sm text-ink-3">{{ claims.length || Number(order.claimed_count ?? 0) }}/{{ Number(order.max_claims ?? 0) }} 人已接单</p>

        <div v-if="claimsLoading" class="mt-4 space-y-2" aria-busy="true">
          <div v-for="n in 2" :key="`claim-skeleton-${n}`" class="skeleton h-14 !rounded-tile"></div>
        </div>
        <div v-else-if="!claims.length" class="empty-state mt-4">
          <div class="empty-state__icon" aria-hidden="true">🙋</div>
          <h4 class="empty-state__title">暂无接单</h4>
          <p class="empty-state__copy">打手接手订单后会出现在这里。</p>
        </div>
        <ul v-else class="mt-4 space-y-2">
          <li v-for="claim in claims" :key="claim.id" class="claims-item">
            <!-- 首行：左侧打手名 + claim 状态标签；右上「首抢」徽标 -->
            <div class="flex items-start justify-between gap-2">
              <div class="flex min-w-0 flex-wrap items-center gap-2">
                <p class="truncate text-sm font-semibold text-ink-1">{{ claimBoosterName(claim) }}</p>
                <span :class="claimStatusMeta(claim).tagClass">{{ claimStatusMeta(claim).label }}</span>
              </div>
              <span v-if="claim.is_first" class="tag shrink-0 !bg-warning-soft !text-warning">首抢</span>
            </div>
            <!-- 次行：左下单号；右下接单时间 -->
            <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
              <p class="text-xs tabular-nums text-ink-3">单号 #{{ claim.id }}</p>
              <p class="text-xs tabular-nums text-ink-3">接单于 {{ formatDateTime(claim.created_at) }}</p>
            </div>
          </li>
        </ul>
      </section>

      <!-- 审核列表 tab：每个已交付的 claim 是一条待审核记录；已结算的归入「已通过」分组 -->
      <section v-if="detailTab === 'review'" class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">审核列表</h2>
        <p class="mt-1 text-sm text-ink-3">待审核 {{ pendingReviewCount }} 人 · 已通过 {{ approvedCount }} 人</p>

        <div v-if="claimsLoading" class="mt-4 space-y-2" aria-busy="true">
          <div v-for="n in 2" :key="`review-skeleton-${n}`" class="skeleton h-14 !rounded-tile"></div>
        </div>

        <div v-else-if="!pendingReviewClaims.length && !settledClaims.length" class="empty-state mt-4">
          <div class="empty-state__icon" aria-hidden="true">📭</div>
          <h4 class="empty-state__title">暂无提交</h4>
          <p class="empty-state__copy">打手点击「结束订单」提交汇报后，会出现在这里等待审核。</p>
        </div>

        <template v-else>
          <!-- 待审核（DELIVERED） -->
          <ul v-if="pendingReviewClaims.length" class="mt-4 space-y-2">
            <li v-for="claim in pendingReviewClaims" :key="claim.id">
              <button
                type="button"
                class="claims-item w-full text-left transition-colors duration-base hover:border-primary"
                @click="openReviewModal(claim)"
              >
                <div class="flex items-start justify-between gap-2">
                  <div class="flex min-w-0 flex-wrap items-center gap-2">
                    <p class="truncate text-sm font-semibold text-ink-1">{{ claimBoosterName(claim) }}</p>
                    <span class="tag !bg-warning-soft !text-warning">待审核</span>
                  </div>
                  <p class="shrink-0 text-xs tabular-nums text-ink-3">单号 #{{ claim.id }}</p>
                </div>
                <p class="mt-1 truncate text-xs text-ink-3">{{ claim.delivery_note || '未填写文字汇报' }}</p>
                <p class="mt-1 text-xs tabular-nums text-ink-3">{{ claim.delivered_at ? `提交于 ${formatDateTime(claim.delivered_at)}` : '' }}</p>
              </button>
            </li>
          </ul>

          <!-- 已通过（SETTLED） -->
          <div v-if="settledClaims.length" class="mt-6">
            <p class="label">已通过</p>
            <ul class="mt-2 space-y-2">
              <li v-for="claim in settledClaims" :key="claim.id">
                <button
                  type="button"
                  class="claims-item w-full text-left transition-colors duration-base hover:border-primary"
                  @click="openReviewModal(claim)"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="flex min-w-0 flex-wrap items-center gap-2">
                      <p class="truncate text-sm font-semibold text-ink-1">{{ claimBoosterName(claim) }}</p>
                      <span class="tag !bg-success-soft !text-success">已结算</span>
                    </div>
                    <p class="shrink-0 text-xs tabular-nums text-ink-3">单号 #{{ claim.id }}</p>
                  </div>
                  <p class="mt-1 truncate text-xs text-ink-3">{{ claim.delivery_note || '未填写文字汇报' }}</p>
                  <p class="mt-1 text-xs tabular-nums text-ink-3">{{ claim.settled_at ? `结算于 ${formatDateTime(claim.settled_at)}` : '' }}</p>
                </button>
              </li>
            </ul>
          </div>
        </template>
      </section>

      <!-- 管理操作 -->
      <section class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">管理操作</h2>
        <div class="mt-4 flex flex-wrap gap-2">
          <button v-if="order.claim_status === 'OPEN'" type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-pause'" @click="controlOrder('pause')">
            {{ actionKey === 'control-pause' ? '处理中…' : '暂停接单' }}
          </button>
          <button v-else-if="order.claim_status === 'PAUSED'" type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-resume'" @click="controlOrder('resume')">
            {{ actionKey === 'control-resume' ? '处理中…' : '恢复接单' }}
          </button>
          <button v-if="order.claim_status !== 'CLOSED' && !order.is_archived" type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-close'" @click="controlOrder('close')">
            {{ actionKey === 'control-close' ? '处理中…' : '截止接单' }}
          </button>
          <button v-if="!order.is_archived" type="button" class="btn-ghost min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-archive'" @click="controlOrder('archive')">
            {{ actionKey === 'control-archive' ? '处理中…' : '归档' }}
          </button>
          <button
            v-if="order.payment_status === 'PAID' && ['CANCELLED', 'DISPUTED'].includes(order.status)"
            type="button"
            class="btn-secondary min-h-[44px] !px-4 !py-2"
            :disabled="actionKey === 'refund'"
            @click="refundOrder"
          >
            {{ actionKey === 'refund' ? '退款中…' : '退款' }}
          </button>
          <!-- 争议处理组：仅 DISPUTED 显示，action 严格取自后端 allowed_actions -->
          <template v-if="order.status === 'DISPUTED'">
            <button
              type="button"
              class="btn-success min-h-[44px] !px-4 !py-2"
              :disabled="actionKey === 'intervene-COMPLETED'"
              @click="interveneOrder('COMPLETED')"
            >
              {{ actionKey === 'intervene-COMPLETED' ? '结算中…' : '完结并结算给打手' }}
            </button>
            <button
              type="button"
              class="btn-secondary min-h-[44px] !px-4 !py-2"
              :disabled="actionKey === 'intervene-CANCELLED'"
              @click="interveneOrder('CANCELLED')"
            >
              {{ actionKey === 'intervene-CANCELLED' ? '处理中…' : '取消订单退款' }}
            </button>
          </template>
          <button type="button" class="btn-danger min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'delete'" @click="deleteThisOrder">
            {{ actionKey === 'delete' ? '删除中…' : '删除订单' }}
          </button>
        </div>
      </section>
    </template>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🔍</div>
      <h2 class="empty-state__title">订单不存在</h2>
      <p class="empty-state__copy">这条订单可能已被删除，或没有访问权限。</p>
    </section>

    <!-- 审核弹窗：查看汇报 + 选择打款金额 -->
    <div v-if="reviewModal" class="modal-scrim modal-scrim--sheet" @click.self="closeReviewModal">
      <div class="modal-card modal-sheet" role="dialog" aria-modal="true" aria-label="审核打手汇报">
        <div class="relative z-10">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-2xl font-semibold text-ink-1">审核 · {{ claimBoosterName(reviewModal.claim) }}</h3>
              <p class="mt-2 text-sm text-ink-2">
                单号 #{{ reviewModal.claim.id }} · 报酬 <span class="font-semibold tabular-nums text-price">{{ priceLabel(order) }}</span>
                <template v-if="reviewModal.claim.status === 'SETTLED'"> · 已结算</template>
              </p>
            </div>
            <button type="button" class="btn-ghost shrink-0 !px-4 !py-2" @click="closeReviewModal">关闭</button>
          </div>

          <div class="mt-5">
            <p class="info-tile__label">汇报说明</p>
            <p class="mt-2 break-words text-sm leading-6 text-ink-2">{{ reviewModal.claim.delivery_note || '打手未填写文字汇报' }}</p>
          </div>

          <div v-if="claimAttachments(reviewModal.claim).length" class="mt-4">
            <p class="info-tile__label">汇报图片（点击放大）</p>
            <div class="mt-2 grid grid-cols-3 gap-3 sm:grid-cols-5">
              <button
                v-for="(image, index) in claimAttachments(reviewModal.claim)"
                :key="image.url + index"
                type="button"
                class="group relative overflow-hidden rounded-tile border border-line-1 bg-surface-2"
                @click="openLightbox(claimAttachments(reviewModal.claim), index)"
              >
                <img :src="image.url" :alt="image.name || '汇报截图'" class="h-24 w-full object-cover transition group-hover:opacity-90" loading="lazy" />
              </button>
            </div>
          </div>

          <!-- 打款区：仅待审核时显示 -->
          <div v-if="reviewModal.claim.status === 'DELIVERED'" class="mt-5 space-y-3">
            <label class="flex cursor-pointer items-start gap-3 rounded-tile border border-line-1 p-4" :class="reviewModal.payout.mode === 'full' ? 'border-primary bg-primary-soft' : ''">
              <input v-model="reviewModal.payout.mode" type="radio" value="full" class="mt-1" />
              <div>
                <p class="text-sm font-semibold text-ink-1">审核通过 · 全额到账</p>
                <p class="mt-1 text-sm tabular-nums text-price">{{ priceLabel(order) }} 全部计入打手余额</p>
              </div>
            </label>
            <label class="flex cursor-pointer items-start gap-3 rounded-tile border border-line-1 p-4" :class="reviewModal.payout.mode === 'partial' ? 'border-primary bg-primary-soft' : ''">
              <input v-model="reviewModal.payout.mode" type="radio" value="partial" class="mt-1" />
              <div class="flex-1">
                <p class="text-sm font-semibold text-ink-1">部分金额到账</p>
                <p class="mt-1 text-sm text-ink-3">手动编辑具体到账金额，剩余部分不予结算</p>
                <div v-if="reviewModal.payout.mode === 'partial'" class="mt-3">
                  <label class="label" for="payout-amount">到账金额</label>
                  <input id="payout-amount" v-model="reviewModal.payout.amount" type="number" min="0.01" step="0.01" class="input" :placeholder="`最多 ${payoutCeiling()}`" :disabled="reviewModal.submitting" />
                </div>
              </div>
            </label>
            <!-- 炸单赔偿金扣除：该单设置了赔偿金时显示（0 ≤ 扣除 ≤ 赔偿金） -->
            <div v-if="orderCompensationAmount > 0" class="rounded-tile border border-line-1 p-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-sm font-semibold text-ink-1">扣除炸单赔偿金</p>
                <p class="text-xs tabular-nums text-ink-3">该单赔偿金 {{ formatPrice(orderCompensationAmount) }}</p>
              </div>
              <div class="mt-3">
                <label class="label" for="payout-deduction">扣除金额（0 ~ {{ formatPrice(orderCompensationAmount) }}）</label>
                <input
                  id="payout-deduction"
                  v-model="reviewModal.payout.deduction"
                  type="number"
                  min="0"
                  :max="orderCompensationAmount"
                  step="0.01"
                  class="input"
                  :disabled="reviewModal.submitting"
                />
                <p class="mt-1.5 text-xs leading-5 text-ink-3">打手炸单时填写，从冻结的炸单赔偿金中扣除；默认 0 不扣。</p>
                <p v-if="payoutDeductionError()" class="mt-1.5 text-xs text-danger">{{ payoutDeductionError() }}</p>
              </div>
            </div>
            <div>
              <label class="label" for="payout-note">打款备注（可选）</label>
              <input id="payout-note" v-model="reviewModal.payout.note" type="text" class="input" maxlength="500" placeholder="例如：目标未全部达成，按 80% 结算" :disabled="reviewModal.submitting" />
            </div>
          </div>

          <div class="mt-6 flex justify-end gap-3">
            <button type="button" class="btn-secondary !px-5 !py-2" @click="closeReviewModal">关闭</button>
            <button v-if="reviewModal.claim.status === 'DELIVERED'" type="button" class="btn-success !px-5 !py-2" :disabled="reviewModal.submitting" @click="submitPayout">
              {{ reviewModal.submitting ? '确认中…' : (reviewModal.payout.mode === 'full' ? '审核通过（全额到账）' : '确认部分到账') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <Lightbox
      :images="lightbox.images"
      :visible="lightbox.visible"
      :start-index="lightbox.index"
      @close="lightbox.visible = false"
    />
  </div>
</template>

<style scoped>
/* 接单/审核条目卡片：与 AdminView 的 .claims-item 保持一致（该类仅在 AdminView 内 scoped 定义） */
.claims-item {
  border-radius: 14px;
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  padding: 12px;
}
</style>
