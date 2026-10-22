<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useOrdersStore } from '@/stores/orders'
import { useAfterSalesStore } from '@/stores/afterSales'
import { compressImageFile } from '@/utils/imageCompress'
import { formatOrderPrice, formatPrice } from '@/utils/display'

// 售后凭证规则：选填，最多 9 张，仅 JPG / PNG，单张 ≤10MB（超过 2MB 自动压缩）
const MAX_FILES = 9
const MAX_SIZE = 10 * 1024 * 1024
const ACCEPT_TYPES = ['image/jpeg', 'image/png']
const REASON_MIN = 5
const REASON_MAX = 500

const route = useRoute()
const router = useRouter()
const ordersStore = useOrdersStore()
const afterSalesStore = useAfterSalesStore()

const orderId = route.params.id

// ── 订单摘要（详情接口拉取，loading / 失败态独立于表单）──
const loadingOrder = ref(true)
const order = computed(() => ordersStore.currentOrder)
const orderError = computed(() => (loadingOrder.value ? '' : (ordersStore.error || '')))

// ── 表单 ──
const reason = ref('')
const amount = ref('')
// 已上传成功的凭证：逐张上传 /after-sale-images 拿 url 后入队，可删除
const images = ref([])
const uploading = ref(false)
const uploadError = ref('')
const submitting = ref(false)
const submitError = ref('')
const submitted = ref(false)

const reasonLen = computed(() => reason.value.length)
const orderPrice = computed(() => Number(order.value?.price ?? 0))
const amountValue = computed(() => Number(amount.value))
const amountError = computed(() => {
  if (amount.value === '') return ''
  if (!Number.isFinite(amountValue.value) || amountValue.value <= 0) {
    return '售后金额必须大于 0'
  }
  if (amountValue.value > orderPrice.value) {
    return `售后金额不能超过订单派单价 ${formatPrice(orderPrice.value)}`
  }
  return ''
})
const canSubmit = computed(() =>
  !submitting.value
  && !uploading.value
  && !loadingOrder.value
  && !orderError.value
  && reasonLen.value >= REASON_MIN
  && reasonLen.value <= REASON_MAX
  && amount.value !== ''
  && !amountError.value
)

async function loadOrder() {
  loadingOrder.value = true
  // 失败时 ordersStore.error 已写好，由 orderError 透出；表单内容保留不丢
  await ordersStore.fetchOrder(orderId)
  loadingOrder.value = false
}

function validateFile(file) {
  if (!ACCEPT_TYPES.includes((file.type || '').toLowerCase())) {
    return '仅支持 JPG / PNG 格式'
  }
  if (file.size > MAX_SIZE) return '单张不能超过 10MB'
  if (file.size <= 0) return '文件为空'
  return ''
}

// 选图即逐张压缩并上传：成功一项入队一项，失败项给出中文原因并跳过，
// 避免「提交时才发现某张图传不上去」。
async function onPick(event) {
  uploadError.value = ''
  const list = Array.from(event.target.files || [])
  event.target.value = ''
  if (!list.length) return

  const remaining = MAX_FILES - images.value.length
  if (remaining <= 0) {
    uploadError.value = `最多上传 ${MAX_FILES} 张凭证，请先删除已上传的图片`
    return
  }
  const toAdd = list.slice(0, remaining)
  if (list.length > remaining) {
    uploadError.value = `最多 ${MAX_FILES} 张，本次仅保留前 ${remaining} 张`
  }

  uploading.value = true
  try {
    for (const file of toAdd) {
      const invalid = validateFile(file)
      if (invalid) {
        uploadError.value = `${file.name}: ${invalid}`
        continue
      }
      // 大图先压缩再上传：手机原图直传弱网下常被网络超时掐断（见 imageCompress.js）
      const prepared = await compressImageFile(file)
      const result = await afterSalesStore.uploadAfterSaleImage(orderId, prepared)
      if (!result.success) {
        uploadError.value = `${file.name}: ${result.error || '上传失败，请移除后重试'}`
        continue
      }
      images.value.push({
        url: result.data?.url || '',
        name: result.data?.name || prepared.name,
      })
    }
  } finally {
    uploading.value = false
  }
}

function removeImage(index) {
  if (uploading.value) return
  images.value.splice(index, 1)
  uploadError.value = ''
}

async function handleSubmit() {
  if (submitting.value) return
  submitError.value = ''

  if (reason.value.trim().length < REASON_MIN) {
    submitError.value = `售后原因至少填写 ${REASON_MIN} 个字`
    return
  }
  if (amount.value === '' || amountError.value) {
    submitError.value = amountError.value || '请填写售后金额'
    return
  }

  submitting.value = true
  try {
    const result = await afterSalesStore.createAfterSale(orderId, {
      reason: reason.value.trim(),
      amount: amountValue.value,
      images: images.value.map((item) => item.url).filter(Boolean),
    })
    if (!result.success) {
      submitError.value = result.error || '提交失败，请稍后重试'
      return
    }
    submitted.value = true
    // 先让用户看到成功反馈再返回，避免「点了没反应」的错觉
    window.setTimeout(() => {
      router.back()
    }, 800)
  } catch (err) {
    submitError.value = err?.message || '提交失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

onMounted(loadOrder)
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-5 sm:p-6 lg:p-8">
      <p class="eyebrow">售后服务</p>
      <h1 class="section-title">申请售后</h1>
      <p class="mt-2 text-sm text-ink-2">对已完成或已取消的订单发起售后，客服核实后会按平台规则处理。</p>
    </section>

    <!-- 订单摘要 -->
    <section class="surface-card p-4 sm:p-6">
      <div v-if="loadingOrder" class="space-y-3" aria-busy="true">
        <div class="skeleton h-5 w-2/5 !rounded-tile"></div>
        <div class="skeleton h-4 w-1/3 !rounded-tile"></div>
      </div>
      <div v-else-if="orderError" class="message-error">
        {{ orderError }}
        <button type="button" class="btn-ghost !px-3 !py-1 text-sm" @click="loadOrder">重试</button>
      </div>
      <div v-else-if="order" class="flex flex-wrap items-end justify-between gap-3">
        <div class="min-w-0">
          <h2 class="truncate text-lg font-semibold text-ink-1">{{ order.title || order.game_name }}</h2>
          <p class="mt-1.5 text-sm text-ink-3">
            订单号 #{{ order.id }}<template v-if="order.game_name"> · {{ order.game_name }}</template>
          </p>
        </div>
        <div class="info-tile info-tile--compact">
          <p class="info-tile__label">派单价</p>
          <p class="info-tile__value text-sm font-semibold tabular-nums text-price">{{ formatOrderPrice(order) }}</p>
        </div>
      </div>
    </section>

    <section v-if="!loadingOrder && !orderError" class="surface-card p-4 sm:p-6 lg:p-8">
      <div class="space-y-5">
        <!-- 售后原因：5-500 字，带计数，不足 5 字禁用提交 -->
        <div>
          <label class="label" for="after-sale-reason">售后原因（{{ REASON_MIN }}-{{ REASON_MAX }} 字）</label>
          <textarea
            id="after-sale-reason"
            v-model="reason"
            rows="4"
            :maxlength="REASON_MAX"
            class="input resize-none"
            placeholder="请说明售后原因，例如：未按约定完成、账号异常等（至少 5 个字）"
          ></textarea>
          <p class="mt-1 text-xs" :class="reasonLen < REASON_MIN ? 'text-danger' : 'text-ink-3'">
            {{ reasonLen }}/{{ REASON_MAX }}
            <template v-if="reasonLen < REASON_MIN">（还需 {{ REASON_MIN - reasonLen }} 字）</template>
          </p>
        </div>

        <!-- 售后凭证：选填，JPG/PNG，最多 9 张，单张 ≤10MB -->
        <div>
          <label class="label" for="after-sale-images">售后凭证（选填，JPG / PNG，最多 {{ MAX_FILES }} 张，单张 ≤10MB）</label>
          <input
            id="after-sale-images"
            type="file"
            accept="image/jpeg,image/png"
            multiple
            class="block w-full text-sm text-ink-2 file:mr-3 file:rounded-full file:border-0 file:bg-surface-3 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-ink-1 hover:file:bg-[var(--surface-3-hover)]"
            :disabled="uploading || images.length >= MAX_FILES"
            @change="onPick"
          />
          <p class="helper-text">
            已上传 {{ images.length }}/{{ MAX_FILES }} 张；超过 2MB 会自动压缩后上传。
            <span v-if="uploading" class="text-primary">上传中…</span>
          </p>
          <p v-if="uploadError" class="message-error mt-2">{{ uploadError }}</p>

          <div v-if="images.length" class="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-5">
            <div
              v-for="(image, index) in images"
              :key="image.url || index"
              class="relative overflow-hidden rounded-tile border border-line-1 bg-surface-2"
            >
              <img :src="image.url" :alt="image.name || `售后凭证 ${index + 1}`" class="h-20 w-full object-cover" />
              <button
                type="button"
                class="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-xs text-white"
                :disabled="uploading"
                :aria-label="`删除凭证 ${index + 1}`"
                :title="`删除凭证 ${index + 1}`"
                @click="removeImage(index)"
              >×</button>
            </div>
          </div>
        </div>

        <!-- 售后金额：>0 且 ≤ 派单价 -->
        <div>
          <label class="label" for="after-sale-amount">售后金额（元）</label>
          <input
            id="after-sale-amount"
            v-model="amount"
            type="number"
            min="0"
            step="0.01"
            :max="orderPrice || undefined"
            class="input"
            :class="{ 'input-error': amountError }"
            placeholder="请输入期望的售后金额"
          />
          <p class="mt-2 text-xs leading-5 text-ink-2">
            售后金额不能超过订单派单价 {{ formatPrice(orderPrice) }}；客服核实后按平台规则处理。
          </p>
          <p v-if="amountError" class="mt-2 text-xs text-danger">{{ amountError }}</p>
        </div>

        <p v-if="submitError" class="message-error">{{ submitError }}</p>
        <p v-if="submitted" class="message-success">售后申请已提交，客服将尽快核实处理</p>
      </div>

      <div class="mt-6 flex gap-3">
        <button type="button" class="btn-secondary flex-1" :disabled="submitting" @click="router.back()">取消</button>
        <button type="button" class="btn-primary flex-1" :disabled="!canSubmit" @click="handleSubmit">
          {{ submitting ? '提交中…' : '提交售后申请' }}
        </button>
      </div>
    </section>
  </div>
</template>
