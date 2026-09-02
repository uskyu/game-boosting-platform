<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import Lightbox from '@/components/Lightbox.vue'
import { useOrdersStore } from '@/stores/orders'
import api from '@/utils/api'
import { formatDateTime, formatPrice } from '@/utils/display'
import { getOrderStatusBadgeClass, getOrderStatusLabel } from '@/utils/order'

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

// 子导航：订单详情 / 报名名单 / 审核列表
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
  const min = o.price_min
  const max = o.price_max
  if (min != null && max != null && Number(min) !== Number(max)) return `${formatPrice(min)} - ${formatPrice(max)}`
  return formatPrice(o.price ?? min ?? max ?? 0)
}

function claimStatusLabel(o) {
  const map = { OPEN: '报名中', PAUSED: '已暂停', FULL: '已满员', CLOSED: '已截止' }
  return map[o.claim_status] || o.claim_status || '-'
}

const orderAttachments = computed(() => normalizeAttachments(order.value?.attachments))
const deliveryAttachments = computed(() => normalizeAttachments(order.value?.delivery_attachments))

// ── 灯箱：订单图片 / 汇报图片共用 ──

const lightbox = ref({ visible: false, images: [], index: 0 })

function openLightbox(images, index) {
  lightbox.value = { visible: true, images, index }
}

// ── 编辑订单（PENDING 可改：PUT /orders/{id} + 附件增删）──

const attachmentTypes = ['image/png', 'image/jpeg', 'image/webp']
const maxAttachmentCount = 5
const maxAttachmentSize = 5 * 1024 * 1024

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
  state.price_min = o.price_min != null ? String(o.price_min) : ''
  state.price_max = o.price_max != null ? String(o.price_max) : ''
  state.max_claims = o.max_claims ?? 1
  state.deadline = toDatetimeLocalValue(o.deadline)
  state.attachments = normalizeAttachments(o.attachments)
}

function openEditPanel() {
  const o = order.value
  if (!o) return
  editPanel.value = {
    title: '', intro: '', description: '', price: '', price_min: '', price_max: '',
    max_claims: 1, deadline: '', attachments: [], newFiles: null,
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
  if (oversized) return `单张图片不能超过5MB：${oversized.name}`
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
  const price = Number(state.price)
  if (!Number.isFinite(price) || price <= 0) {
    state.error = '请填写大于 0 的价格'
    return
  }
  if (state.price_min && state.price_max && Number(state.price_min) > Number(state.price_max)) {
    state.error = '价格区间的最低价不能高于最高价'
    return
  }
  const maxClaims = Number(state.max_claims)
  if (!Number.isInteger(maxClaims) || maxClaims < 1 || maxClaims > 100) {
    state.error = '最大抢单人数需为 1-100 的整数'
    return
  }

  state.submitting = true
  const result = await ordersStore.editOrder(order.value.id, {
    title: state.title.trim() || null,
    intro: state.intro.trim() || null,
    description: state.description.trim() || null,
    price,
    price_min: state.price_min ? Number(state.price_min) : null,
    price_max: state.price_max ? Number(state.price_max) : null,
    max_claims: maxClaims,
    deadline: state.deadline || null,
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

// ── 审核列表 + 审核弹窗（DELIVERED=待审核 / COMPLETED=已通过）──

// 提交记录：交付打手的结束汇报（当前模型每单一条）
const reviewSubmissions = computed(() => {
  if (!order.value || !['DELIVERED', 'COMPLETED'].includes(order.value.status)) return []
  return [{
    booster: order.value.booster,
    delivered_at: order.value.delivered_at,
    status: order.value.status, // DELIVERED=待审核, COMPLETED=已通过
    note: order.value.delivery_note || '',
    attachments: deliveryAttachments.value,
  }]
})

const pendingReviewCount = computed(() => (order.value?.status === 'DELIVERED' && order.value.booster_id ? 1 : 0))
const approvedCount = computed(() => (order.value?.status === 'COMPLETED' && order.value.booster_id ? 1 : 0))

// 审核弹窗：查看汇报 + 打款（全额 / 部分金额 + 备注）
const reviewModal = ref(null) // { item, payout: { mode, amount, note }, submitting }

function openReviewModal(item) {
  reviewModal.value = { item, payout: { mode: 'full', amount: '', note: '' }, submitting: false }
}

function closeReviewModal() {
  if (!reviewModal.value?.submitting) reviewModal.value = null
}

function payoutAmountError() {
  const payout = reviewModal.value?.payout
  if (!payout || payout.mode !== 'partial') return ''
  const amount = Number(payout.amount)
  if (!Number.isFinite(amount) || amount <= 0) return '请填写大于 0 的到账金额'
  const price = Math.max(Number(order.value?.price ?? 0), Number(order.value?.price_max ?? 0))
  if (price && amount > price) return `到账金额不能超过订单报酬 ${formatPrice(price)}`
  return ''
}

async function submitPayout() {
  if (!reviewModal.value || reviewModal.value.submitting) return
  const payout = reviewModal.value.payout
  const amountError = payoutAmountError()
  if (amountError) {
    message.value = { type: 'error', text: amountError }
    return
  }
  const note = payout.note.trim() || null
  const payload = payout.mode === 'partial'
    ? { amount: Number(payout.amount), note }
    : (note ? { note } : null)
  reviewModal.value.submitting = true
  const result = await ordersStore.confirmOrder(order.value.id, payload)
  reviewModal.value.submitting = false
  if (result.success) {
    const text = payout.mode === 'partial'
      ? `已按 ${formatPrice(Number(payout.amount))} 部分到账，订单完结`
      : '审核通过，全额已计入打手余额'
    message.value = { type: 'success', text }
    closeReviewModal()
    await fetchOrder()
  } else {
    message.value = { type: 'error', text: formatApiError(result.error) || '确认失败' }
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
  // 并行拉取：订单详情与报名名单互不依赖（远程库延迟下串行会翻倍）
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
        <h1 class="text-xl font-semibold text-ink-1">{{ order.title || `${order.current_rank || '?'} → ${order.target_rank || '?'}` }}</h1>
        <p class="mt-1 text-sm text-ink-3">
          #{{ order.id }} · {{ order.game_name }}<template v-if="order.current_rank || order.target_rank"> · {{ order.current_rank || '?' }} → {{ order.target_rank || '?' }}</template>
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

      <!-- 子导航：订单详情 / 报名名单 / 审核列表 -->
      <nav class="tab-bar" aria-label="派单处理视图">
        <button type="button" :class="detailTab === 'info' ? 'tab-pill-active' : 'tab-pill'" @click="detailTab = 'info'">订单详情</button>
        <button type="button" :class="detailTab === 'claims' ? 'tab-pill-active' : 'tab-pill'" @click="detailTab = 'claims'">报名名单</button>
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
        <p class="mt-2 text-sm text-ink-3">订单被确认前可修改标题、简介、报酬、名额与截止时间，也可补充或删除图片。</p>
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
              <label class="label" for="edit-max-claims">最大抢单人数</label>
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
          <div class="grid gap-4 sm:grid-cols-3">
            <div>
              <label class="label" for="edit-price">固定价格</label>
              <input id="edit-price" v-model="editPanel.price" type="number" min="0.01" step="0.01" class="input" placeholder="价格" :disabled="editPanel.submitting" />
            </div>
            <div>
              <label class="label" for="edit-price-min">区间最低</label>
              <input id="edit-price-min" v-model="editPanel.price_min" type="number" min="0.01" step="0.01" class="input" placeholder="最低" :disabled="editPanel.submitting" />
            </div>
            <div>
              <label class="label" for="edit-price-max">区间最高</label>
              <input id="edit-price-max" v-model="editPanel.price_max" type="number" min="0.01" step="0.01" class="input" placeholder="最高" :disabled="editPanel.submitting" />
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

      <!-- 报名名单 tab -->
      <section v-if="detailTab === 'claims'" class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">报名名单</h2>
        <p class="mt-1 text-sm text-ink-3">{{ claims.length || Number(order.claimed_count ?? 0) }}/{{ Number(order.max_claims ?? 0) }} 人已报名</p>

        <div v-if="claimsLoading" class="mt-4 space-y-2" aria-busy="true">
          <div v-for="n in 2" :key="`claim-skeleton-${n}`" class="skeleton h-14 !rounded-tile"></div>
        </div>
        <div v-else-if="!claims.length" class="empty-state mt-4">
          <div class="empty-state__icon" aria-hidden="true">🙋</div>
          <h4 class="empty-state__title">暂无报名</h4>
          <p class="empty-state__copy">打手确认订单后会出现在这里。</p>
        </div>
        <ul v-else class="mt-4 space-y-2">
          <li v-for="claim in claims" :key="claim.id" class="claims-item">
            <div class="flex items-center justify-between gap-2">
              <div class="flex min-w-0 items-center gap-2">
                <p class="truncate text-sm font-semibold text-ink-1">{{ claim.booster_nickname || `#${claim.booster_id}` }}</p>
                <span v-if="claim.is_first" class="tag !bg-warning-soft !text-warning">首抢</span>
                <span v-if="claim.booster_id === order.booster_id" class="tag !bg-success-soft !text-success">接单人</span>
              </div>
              <p class="shrink-0 text-xs tabular-nums text-ink-3">{{ formatDateTime(claim.created_at) }}</p>
            </div>
          </li>
        </ul>
      </section>

      <!-- 审核列表 tab：交付打手提交的结束汇报 -->
      <section v-if="detailTab === 'review'" class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">审核列表</h2>
        <p class="mt-1 text-sm text-ink-3">待审核 {{ pendingReviewCount }} 人 · 已通过 {{ approvedCount }} 人</p>

        <div v-if="!reviewSubmissions.length" class="empty-state mt-4">
          <div class="empty-state__icon" aria-hidden="true">📭</div>
          <h4 class="empty-state__title">暂无提交</h4>
          <p class="empty-state__copy">打手点击「结束订单」提交汇报后，会出现在这里等待审核。</p>
        </div>

        <ul v-else class="mt-4 space-y-2">
          <li v-for="(item, index) in reviewSubmissions" :key="index">
            <button
              type="button"
              class="claims-item w-full text-left"
              :class="item.status === 'DELIVERED' ? 'cursor-pointer hover:border-primary' : 'cursor-default opacity-80'"
              @click="openReviewModal(item)"
            >
              <div class="flex items-center justify-between gap-2">
                <div class="flex min-w-0 items-center gap-2">
                  <p class="truncate text-sm font-semibold text-ink-1">{{ item.booster?.username || '未指派打手' }}</p>
                  <span v-if="item.status === 'DELIVERED'" class="tag !bg-warning-soft !text-warning">待审核</span>
                  <span v-else class="tag !bg-success-soft !text-success">已通过</span>
                </div>
                <p class="shrink-0 text-xs tabular-nums text-ink-3">{{ item.delivered_at ? formatDateTime(item.delivered_at) : '' }}</p>
              </div>
              <p class="mt-1 truncate text-xs text-ink-3">{{ item.note || '未填写文字汇报' }}</p>
            </button>
          </li>
        </ul>
      </section>

      <!-- 管理操作 -->
      <section class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">管理操作</h2>
        <div class="mt-4 flex flex-wrap gap-2">
          <button v-if="order.claim_status === 'OPEN'" type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-pause'" @click="controlOrder('pause')">
            {{ actionKey === 'control-pause' ? '处理中…' : '暂停报名' }}
          </button>
          <button v-else-if="order.claim_status === 'PAUSED'" type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-resume'" @click="controlOrder('resume')">
            {{ actionKey === 'control-resume' ? '处理中…' : '恢复报名' }}
          </button>
          <button v-if="order.claim_status !== 'CLOSED' && !order.is_archived" type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="actionKey === 'control-close'" @click="controlOrder('close')">
            {{ actionKey === 'control-close' ? '处理中…' : '截止报名' }}
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
              <h3 class="text-2xl font-semibold text-ink-1">审核 · {{ reviewModal.item.booster?.username || '打手' }}</h3>
              <p class="mt-2 text-sm text-ink-2">
                报酬 <span class="font-semibold tabular-nums text-price">{{ priceLabel(order) }}</span>
                <template v-if="reviewModal.item.status === 'COMPLETED'"> · 已通过</template>
              </p>
            </div>
            <button type="button" class="btn-ghost shrink-0 !px-4 !py-2" @click="closeReviewModal">关闭</button>
          </div>

          <div class="mt-5">
            <p class="info-tile__label">汇报说明</p>
            <p class="mt-2 break-words text-sm leading-6 text-ink-2">{{ reviewModal.item.note || '打手未填写文字汇报' }}</p>
          </div>

          <div v-if="reviewModal.item.attachments.length" class="mt-4">
            <p class="info-tile__label">汇报图片（点击放大）</p>
            <div class="mt-2 grid grid-cols-3 gap-3 sm:grid-cols-5">
              <button
                v-for="(image, index) in reviewModal.item.attachments"
                :key="image.url + index"
                type="button"
                class="group relative overflow-hidden rounded-tile border border-line-1 bg-surface-2"
                @click="openLightbox(reviewModal.item.attachments, index)"
              >
                <img :src="image.url" :alt="image.name || '汇报截图'" class="h-24 w-full object-cover transition group-hover:opacity-90" loading="lazy" />
              </button>
            </div>
          </div>

          <!-- 打款区：仅待审核时显示 -->
          <div v-if="reviewModal.item.status === 'DELIVERED'" class="mt-5 space-y-3">
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
                  <input id="payout-amount" v-model="reviewModal.payout.amount" type="number" min="0.01" step="0.01" class="input" :placeholder="`最多 ${Math.max(Number(order.price ?? 0), Number(order.price_max ?? 0))}`" :disabled="reviewModal.submitting" />
                </div>
              </div>
            </label>
            <div>
              <label class="label" for="payout-note">打款备注（可选）</label>
              <input id="payout-note" v-model="reviewModal.payout.note" type="text" class="input" maxlength="500" placeholder="例如：目标未全部达成，按 80% 结算" :disabled="reviewModal.submitting" />
            </div>
          </div>

          <div class="mt-6 flex justify-end gap-3">
            <button type="button" class="btn-secondary !px-5 !py-2" @click="closeReviewModal">关闭</button>
            <button v-if="reviewModal.item.status === 'DELIVERED'" type="button" class="btn-success !px-5 !py-2" :disabled="reviewModal.submitting" @click="submitPayout">
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
/* 报名/审核条目卡片：与 AdminView 的 .claims-item 保持一致（该类仅在 AdminView 内 scoped 定义） */
.claims-item {
  border-radius: 14px;
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  padding: 12px;
}
</style>
