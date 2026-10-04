<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useGamesStore } from '@/stores/games'
import { useOrdersStore } from '@/stores/orders'
import { useOrderTemplatesStore } from '@/stores/orderTemplates'
import { useAuthStore } from '@/stores/auth'
import { useServiceFeeStore } from '@/stores/serviceFee'
import api from '@/utils/api'
import { formatFeeRate, formatMoneyFixed, parsePayoutDelay, serviceFeeBreakdown } from '@/utils/display'
import { getPublishButtonLabel } from '@/utils/humanCopy'
import { cleanTemplatePayload, resetOrderForm } from '@/utils/orderTemplates'

const router = useRouter()
const route = useRoute()
const gamesStore = useGamesStore()
const ordersStore = useOrdersStore()
const templatesStore = useOrderTemplatesStore()
const authStore = useAuthStore()
const serviceFeeStore = useServiceFeeStore()
const isAdmin = computed(() => authStore.isAdmin)
const globalServiceFeeRate = ref(0)
const individualServiceFeeEnabled = ref(false)
const templateSheetOpen = ref(false)
const saveTemplateOpen = ref(false)
const templateName = ref('')
const templateMessage = ref('')
const errorMessage = ref('')
const submitting = ref(false)
const createActionsBar = ref(null)

function templateFields() {
  const fields = cleanTemplatePayload(formData.value)
  delete fields.attachments
  return fields
}

async function saveTemplate() {
  if (!templateName.value.trim()) { templateMessage.value = '请输入模板名称'; return }
  const result = await templatesStore.createTemplate(templateName.value, templateFields())
  if (!result.success) { templateMessage.value = result.error; return }
  saveTemplateOpen.value = false; templateName.value = ''; templateMessage.value = '模板已保存'
}

function applyTemplate(template) {
  formData.value = resetOrderForm({
    game_id: null, game_name: '', title: '', price: '', service_type: '陪玩', notes: '',
    description_raw: '', boss_contact: '', max_claims: 1, compensation_enabled: false,
    compensation_amount: '', payout_delay_days: '', payout_delay_hours: '',
    require_delivery_image: true,
    attachments: formData.value.attachments,
  }, template)
  handleGameChange()
  if (individualServiceFeeEnabled.value && globalServiceFeeRate.value > 0) {
    formData.value.service_fee_enabled = true
  }
  templateSheetOpen.value = false
  templateMessage.value = `已应用模板：${template.name}`
}

async function removeTemplate(template) {
  await templatesStore.deleteTemplate(template.id)
}

const successMessage = ref('')
const attachmentTypes = ['image/png', 'image/jpeg', 'image/webp']
const maxAttachmentCount = 5
const maxAttachmentSize = 10 * 1024 * 1024
const uploadProgress = ref('')

// 真正待上传的图片只有 File 对象；从原单带过来的附件是 {url,name,...} 对象，
// 直接进 payload.attachments（OrderCreate schema 本就支持），不再走创建后上传。
function isUploadFile(item) {
  return typeof File !== 'undefined' && item instanceof File
}

function validateAttachments(files) {
  const selected = Array.from(files || []).filter(isUploadFile)
  if (selected.length > maxAttachmentCount) return '订单最多上传5张图片'
  const invalid = selected.find((file) => !attachmentTypes.includes((file.type || '').toLowerCase()))
  if (invalid) return `仅支持 PNG、JPEG、WebP 图片：${invalid.name}`
  const oversized = selected.find((file) => file.size > maxAttachmentSize)
  if (oversized) return `单张图片不能超过10MB：${oversized.name}`
  return ''
}

// 从原单带过来的附件条目（非 File）：URL 形状见 schemas/order.py 的 OrderAttachment
// （{url,name,size,content_type}），OrderCreate.attachments 接受的正是这个形态。
function carriedAttachments() {
  return Array.from(formData.value.attachments || [])
    .filter((item) => !isUploadFile(item) && item && typeof item === 'object' && typeof item.url === 'string')
}

async function uploadAttachments(orderId, files) {
  const selected = Array.from(files || []).filter(isUploadFile)
  for (let index = 0; index < selected.length; index += 1) {
    const body = new FormData()
    body.append('attachment', selected[index])
    uploadProgress.value = `${index + 1}/${selected.length}（0%）`
    await api.post(`/orders/${orderId}/attachments`, body, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const percent = event.total ? Math.round((event.loaded / event.total) * 100) : 0
        uploadProgress.value = `${index + 1}/${selected.length}（${percent}%）`
      },
    })
  }
  uploadProgress.value = ''
}

const formData = ref({
  game_id: null,
  game_name: '',
  title: '',
  price: '',
  service_type: '陪玩',
  notes: '',
  ai_tags: null,
  attachments: null,
  description_raw: '',
  // 老板ID / 炸单赔偿金 / 到账时效 / 最大接单人数
  boss_contact: '',
  max_claims: 1,
  compensation_enabled: false,
  compensation_amount: '',
  payout_delay_days: '',
  payout_delay_hours: '',
  // 老板开关：必须上传完成截图才能申请结单（默认开启）
  require_delivery_image: true,
  service_fee_enabled: false,
  service_fee_rate: '',
})

// 到账时效快捷选项：点选后填入天/小时输入框
const payoutDelayShortcuts = [
  { label: '1天', days: 1, hours: '' },
  { label: '3天', days: 3, hours: '' },
  { label: '7天', days: 7, hours: '' },
  { label: '12小时', days: '', hours: 12 },
  { label: '1天12小时', days: 1, hours: 12 },
]

function applyPayoutDelayShortcut(shortcut) {
  formData.value.payout_delay_days = shortcut.days
  formData.value.payout_delay_hours = shortcut.hours
}

const serviceFeeShortcuts = [
  { label: '5%', rate: '5' },
  { label: '8%', rate: '8' },
  { label: '10%', rate: '10' },
]

const serviceFeePreview = computed(() => {
  if (!formData.value.service_fee_enabled) return null
  const price = Number(formData.value.price)
  if (!Number.isFinite(price) || price <= 0) return null
  const typed = Number(formData.value.service_fee_rate)
  const rate = formData.value.service_fee_rate === '' ? globalServiceFeeRate.value : typed
  const breakdown = serviceFeeBreakdown(price, Number.isFinite(rate) ? rate : 0)
  return { price, ratePercent: breakdown.ratePercent, fee: breakdown.fee, net: breakdown.net }
})

// 游戏下拉：只列管理员上架的游戏；仅有一个时默认选中
const catalogGames = computed(() => gamesStore.games)
const selectedGame = computed(() => (
  formData.value.game_id ? gamesStore.getGameById(Number(formData.value.game_id)) : null
))
const isSubmitting = computed(() => ordersStore.loading)

// 余额托管提示：发布后冻结 ¥price × max_claims，完结后结算给打手
const escrowHint = computed(() => {
  const price = Number(formData.value.price)
  const maxClaims = Number(formData.value.max_claims) || 1
  if (Number.isFinite(price) && price > 0) {
    return `发布后将冻结 ¥${(price * maxClaims).toFixed(2)} 托管，完结后结算给打手`
  }
  return '发布后将冻结 ¥发单价格 × 接单人数 的托管金额，完结后结算给打手'
})

const canPublish = computed(() => {
  return Boolean(
    selectedGame.value
    && !gameOffline.value
    && formData.value.description_raw.trim()
    && Number(formData.value.price) > 0
  )
})

function handleGameChange() {
  formData.value.service_type = '陪玩'
  const game = selectedGame.value
  formData.value.game_name = game?.name || ''
}

// ── 重建 / 续单预填（URL: /orders/create?recycle=<orderId>&mode=rebuild|repeat）──
// 原游戏已下架时保留 game_id/game_name 并告警，发布被 canPublish 拦截
const recycleOrderId = ref(null)
const recycleMode = ref('')
const gameOffline = computed(() => (
  Boolean(formData.value.game_id)
  && !catalogGames.value.some((game) => Number(game.id) === Number(formData.value.game_id))
))

async function prefillFromOrder(orderId, mode) {
  recycleOrderId.value = orderId
  recycleMode.value = mode
  const result = await ordersStore.fetchOrder(orderId)
  const order = result?.data || ordersStore.currentOrder
  if (!order) {
    errorMessage.value = result?.error || '原订单读取失败，请手动填写后发布'
    return
  }
  const price = order.price != null ? String(order.price) : ''
  const compensation = Number(order.compensation_amount || 0)
  formData.value = {
    ...formData.value,
    game_id: order.game_id ?? null,
    game_name: order.game_name || '',
    // 续单默认指定同一位打手；重建沿用原标题
    title: mode === 'repeat'
      ? `指定${order.booster?.username || '打手'}`
      : (order.title || ''),
    price,
    description_raw: order.description_raw || '',
    notes: order.notes || '',
    boss_contact: order.boss_contact || '',
    max_claims: Number(order.max_claims) || 1,
    compensation_enabled: compensation > 0,
    compensation_amount: compensation > 0 ? String(order.compensation_amount) : '',
    payout_delay_days: order.payout_delay_days ?? '',
    payout_delay_hours: order.payout_delay_hours ?? '',
    require_delivery_image: Boolean(order.require_delivery_image),
    // 原单附件以对象形态直接进 payload.attachments（含 /uploads/orders/ URL），
    // 用户仍可继续选新图一起发布
    attachments: Array.isArray(order.attachments) ? [...order.attachments] : [],
  }
  if (isAdmin.value && individualServiceFeeEnabled.value) {
    const feeRate = Number(order.service_fee_rate || 0)
    formData.value.service_fee_enabled = feeRate > 0
    formData.value.service_fee_rate = feeRate > 0 ? String(order.service_fee_rate) : ''
  }
  // 游戏仍在架时同步 catalog 里的最新名称；已下架则保留原值并告警
  if (selectedGame.value) {
    handleGameChange()
  }
}

async function publishOrder() {
  if (submitting.value) return
  if (!canPublish.value) {
    errorMessage.value = '游戏、需求和价格都是必填项。'
    return
  }

  submitting.value = true
  try {
  uploadProgress.value = ''

  const attachmentError = validateAttachments(formData.value.attachments)
  if (attachmentError) {
    errorMessage.value = attachmentError
    return
  }

  const bossContact = formData.value.boss_contact.trim()
  if (bossContact.length > 64) {
    errorMessage.value = '老板ID不能超过 64 个字符'
    return
  }

  let compensationAmount = null
  if (formData.value.compensation_enabled) {
    compensationAmount = Number(formData.value.compensation_amount)
    if (!Number.isFinite(compensationAmount) || compensationAmount <= 0) {
      errorMessage.value = '炸单赔偿金需为大于 0 的金额'
      return
    }
  }

  const payoutDelay = parsePayoutDelay(formData.value.payout_delay_days, formData.value.payout_delay_hours)
  if (payoutDelay.error) {
    errorMessage.value = payoutDelay.error
    return
  }

  const maxClaims = Number(formData.value.max_claims)
  if (!Number.isInteger(maxClaims) || maxClaims < 1 || maxClaims > 100) {
    errorMessage.value = '最大接单人数需为 1-100 的整数'
    return
  }

  let serviceFeeRate = null
  if (isAdmin.value && individualServiceFeeEnabled.value && formData.value.service_fee_enabled && formData.value.service_fee_rate !== '') {
    serviceFeeRate = Number(formData.value.service_fee_rate)
    if (!Number.isFinite(serviceFeeRate) || serviceFeeRate < 0 || serviceFeeRate > 100) {
      errorMessage.value = '服务费费率需为 0-100 之间的数值'
      return
    }
  }

  const title = formData.value.title.trim()
  const payload = {
    game_id: selectedGame.value.id,
    game_name: selectedGame.value.name,
    title: title || null,
    price: Number(formData.value.price),
    description_raw: formData.value.description_raw.trim(),
    service_type: '陪玩',
    notes: formData.value.notes.trim() || null,
    boss_contact: bossContact || null,
    max_claims: maxClaims,
    payout_delay_days: payoutDelay.days,
    payout_delay_hours: payoutDelay.hours,
    require_delivery_image: formData.value.require_delivery_image,
  }
  if (compensationAmount != null) {
    payload.compensation_amount = compensationAmount
  }
  // 从原单带过来的附件（URL 条目）直接进 payload；File 仍走创建后上传
  if (isAdmin.value && individualServiceFeeEnabled.value) {
    payload.service_fee_enabled = formData.value.service_fee_enabled
    if (serviceFeeRate != null) payload.service_fee_rate = serviceFeeRate
  }
  const keptAttachments = carriedAttachments()
  if (keptAttachments.length) {
    payload.attachments = keptAttachments
  }

  const result = await ordersStore.createOrder(payload)
  if (!result.success) {
    errorMessage.value = result.error
    return
  }

  try {
    await uploadAttachments(result.data.id, formData.value.attachments)
  } catch (error) {
    uploadProgress.value = ''
    errorMessage.value = `订单已创建，但图片上传失败：${error.message || '请稍后重试'}`
    return
  }

  successMessage.value = '需求发出去了，等陪玩接单。'
  window.setTimeout(() => {
    router.push({ name: 'orders' })
  }, 900)
  } finally {
    submitting.value = false
  }
}

watch(
  catalogGames,
  (games) => {
    if (formData.value.game_id == null && games.length === 1) {
      formData.value.game_id = games[0].id
      handleGameChange()
    }
  },
  { immediate: true }
)

onMounted(async () => {
  await Promise.all([gamesStore.ensureCatalog(), templatesStore.fetchTemplates()])
  if (isAdmin.value) {
    const feeResult = await serviceFeeStore.fetchSettings(true)
    if (feeResult.success) {
      globalServiceFeeRate.value = Number(feeResult.data?.service_fee_rate ?? 0)
      individualServiceFeeEnabled.value = Boolean(feeResult.data?.individual_service_fee_enabled)
      if (individualServiceFeeEnabled.value && globalServiceFeeRate.value > 0) {
        formData.value.service_fee_enabled = true
      }
    }
  }
  // 重建 / 续单：等 catalog 就绪后再拉原单，避免游戏下拉还没渲染
  const recycleId = Number(route.query.recycle)
  if (Number.isInteger(recycleId) && recycleId > 0) {
    const mode = route.query.mode === 'repeat' ? 'repeat' : 'rebuild'
    await prefillFromOrder(recycleId, mode)
    if (mode === 'rebuild' && !errorMessage.value) {
      await nextTick()
      window.requestAnimationFrame(() => {
        createActionsBar.value?.scrollIntoView({ behavior: 'smooth', block: 'end' })
      })
    }
  }
})
</script>

<template>
  <div class="page-shell page-shell--create space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <p class="eyebrow">发布订单</p>
      <h1 class="section-title">填好需求，直接发布</h1>
      <p class="section-copy max-w-3xl">
        选游戏、写需求和价格，一键发布到大厅；打手接单后你去审核打款就行。
      </p>
      <div class="message-info mt-5 !mb-0">{{ escrowHint }}</div>
    </section>

    <!-- 重建 / 续单：按原单预填后的提示横幅 -->
    <div v-if="recycleOrderId" class="message-info">
      已根据订单 #{{ recycleOrderId }} 预填（{{ recycleMode === 'repeat' ? '续单' : '重建' }}），可修改后发布
    </div>

    <div v-if="errorMessage" class="message-error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="message-success">{{ successMessage }}</div>

    <div v-if="templateMessage" class="message-info">{{ templateMessage }}</div>

    <section class="surface-card flex flex-wrap items-center justify-between gap-3 p-4">
      <div><p class="text-sm font-semibold text-ink-1">订单模板</p><p class="text-xs text-ink-3">保存常用订单字段，附件不会保存</p></div>
      <div class="flex gap-2">
        <button type="button" class="filter-pill" @click="templateSheetOpen = true">选择模板</button>
        <button type="button" class="filter-pill-active" @click="saveTemplateOpen = true">保存为模板</button>
      </div>
    </section>

      <div v-if="templateSheetOpen" class="modal-scrim modal-scrim--sheet" @click.self="templateSheetOpen = false">
      <section class="modal-card modal-sheet mx-auto max-w-lg" role="dialog" aria-modal="true" aria-label="选择订单模板">
        <div class="mb-4 flex items-center justify-between"><h2 class="text-lg font-semibold">选择订单模板</h2><button type="button" class="filter-pill" @click="templateSheetOpen = false">关闭</button></div>
        <p v-if="!templatesStore.hasTemplates" class="text-sm text-ink-3">暂无模板</p>
        <div v-for="template in templatesStore.templates" :key="template.id" class="flex items-center justify-between border-b border-line-1 py-3">
          <button type="button" class="text-left text-sm font-semibold text-primary" @click="applyTemplate(template)">{{ template.name }}</button>
          <button type="button" class="text-xs text-danger" @click="removeTemplate(template)">删除</button>
        </div>
      </section>
    </div>

    <div v-if="saveTemplateOpen" class="modal-scrim" @click.self="saveTemplateOpen = false">
      <section class="modal-card max-w-md" role="dialog" aria-modal="true" aria-label="保存订单模板"><h2 class="text-lg font-semibold">保存订单模板</h2><input v-model="templateName" class="input mt-4" maxlength="50" placeholder="模板名称" @keyup.enter="saveTemplate" /><p v-if="templateMessage" class="mt-2 text-sm text-danger">{{ templateMessage }}</p><div class="mt-5 flex justify-end gap-2"><button type="button" class="filter-pill" @click="saveTemplateOpen = false">取消</button><button type="button" class="btn-primary" @click="saveTemplate">保存</button></div></section>
    </div>

    <section class="surface-card p-6 sm:p-8">
      <div class="grid gap-5 sm:grid-cols-2">
        <div>
          <label class="label" for="create-game">游戏 *</label>
          <select id="create-game" v-model="formData.game_id" class="input" @change="handleGameChange">
            <option :value="null" disabled>请选择游戏</option>
            <option v-for="game in catalogGames" :key="game.id" :value="game.id">{{ game.name }}</option>
          </select>
          <p class="mt-1 text-xs text-ink-3">只能选择管理员上架的游戏</p>
          <!-- 原单游戏已被下架：保留原值但禁止发布，逼用户重新选 -->
          <p v-if="gameOffline" class="mt-1 text-xs font-semibold text-warning">原游戏已下架，请重新选择</p>
        </div>
        <div>
          <label class="label" for="create-service-type">服务类型</label>
          <input id="create-service-type" :value="formData.service_type" type="text" class="input" readonly />
        </div>

        <div class="sm:col-span-2">
          <label class="label" for="create-title">订单标题（可选）</label>
          <input id="create-title" v-model="formData.title" type="text" maxlength="200" class="input" placeholder="例如：三角洲烽火地带上分单" />
        </div>

        <div class="sm:col-span-2">
          <label class="label" for="create-description">需求描述 *</label>
          <textarea
            id="create-description"
            v-model="formData.description_raw"
            rows="5"
            class="input resize-none"
            placeholder="例如：王者荣耀，星耀三上王者，希望晚上开打，要求打野位。"
          ></textarea>
        </div>

        <div>
          <label class="label" for="create-price">发单价格 *</label>
          <input id="create-price" v-model="formData.price" type="number" min="1" step="0.01" class="input" placeholder="例如：88" />
        </div>
        <div>
          <label class="label" for="create-max-claims">最大接单人数</label>
          <input id="create-max-claims" v-model="formData.max_claims" type="number" min="1" max="100" step="1" class="input" placeholder="例如：1" />
        </div>

        <div>
          <label class="label" for="create-boss-contact">老板ID（可选）</label>
          <input id="create-boss-contact" v-model="formData.boss_contact" type="text" class="input" maxlength="64" placeholder="接单后打手可见，用于添加你为好友" />
        </div>
        <div>
          <label class="label" for="create-payout-delay-days">到账时效（都不填=不设置）</label>
          <div class="flex items-center gap-2">
            <input id="create-payout-delay-days" v-model="formData.payout_delay_days" type="number" min="0" max="30" step="1" class="input" placeholder="天（0-30）" />
            <span class="text-sm text-ink-3">天</span>
            <input id="create-payout-delay-hours" v-model="formData.payout_delay_hours" type="number" min="0" max="23" step="1" class="input" placeholder="小时（0-23）" />
            <span class="text-sm text-ink-3">小时</span>
          </div>
          <div class="mt-2 flex flex-wrap gap-2">
            <button
              v-for="shortcut in payoutDelayShortcuts"
              :key="shortcut.label"
              type="button"
              class="filter-pill"
              @click="applyPayoutDelayShortcut(shortcut)"
            >
              {{ shortcut.label }}
            </button>
          </div>
        </div>

        <!-- 结单必须上传截图：老板开关，默认开启 -->
        <div class="sm:col-span-2 rounded-tile border border-line-1 p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-semibold text-ink-1">结单必须上传完成截图</p>
              <p class="mt-1 text-xs leading-5 text-ink-3">开启后，打手必须先上传至少 1 张完成截图才能提交结单申请，避免误点直接结单。</p>
            </div>
            <button
              type="button"
              :class="formData.require_delivery_image ? 'filter-pill-active' : 'filter-pill'"
              :aria-pressed="formData.require_delivery_image"
              @click="formData.require_delivery_image = !formData.require_delivery_image"
            >
              {{ formData.require_delivery_image ? '已开启' : '未开启' }}
            </button>
          </div>
        </div>

        <div v-if="isAdmin && !individualServiceFeeEnabled" class="sm:col-span-2 rounded-tile border border-line-1 bg-surface-2 p-4 text-sm text-ink-2">
          后台已关闭单独服务费设置，本订单服务费率为 0。
        </div>

        <!-- 逐单服务费由后台总开关控制 -->
        <div v-if="isAdmin && individualServiceFeeEnabled" class="sm:col-span-2 rounded-tile border border-line-1 p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-semibold text-ink-1">本单服务费</p>
              <p class="mt-1 text-xs leading-5 text-ink-3">开启后按全局费率收取；也可为本单自定义费率或关闭。</p>
            </div>
            <button
              type="button"
              :class="formData.service_fee_enabled ? 'filter-pill-active' : 'filter-pill'"
              :aria-pressed="formData.service_fee_enabled"
              @click="formData.service_fee_enabled = !formData.service_fee_enabled"
            >
              {{ formData.service_fee_enabled ? '已开启' : '未开启' }}
            </button>
          </div>
          <p class="mt-2 text-xs leading-5 text-ink-2">
            <template v-if="globalServiceFeeRate > 0">当前全局服务费 {{ globalServiceFeeRate }}%；自定义费率留空时按此收取。</template>
            <template v-else>后台全局服务费为 0%；如需收取，请填写自定义费率。</template>
          </p>
          <div v-if="formData.service_fee_enabled" class="mt-3">
            <label class="label" for="create-service-fee-rate">自定义费率（%，留空 = 按全局）</label>
            <div class="flex flex-wrap items-center gap-2">
              <input id="create-service-fee-rate" v-model="formData.service_fee_rate" type="number" min="0" max="100" step="0.1" class="input max-w-[140px]" placeholder="例如：8" />
              <button v-for="shortcut in serviceFeeShortcuts" :key="shortcut.label" type="button" class="filter-pill" @click="formData.service_fee_rate = shortcut.rate">
                {{ shortcut.label }}
              </button>
            </div>
            <p v-if="serviceFeePreview" class="mt-2 text-xs leading-5 text-ink-2">
              订单金额 {{ formatMoneyFixed(serviceFeePreview.price) }} · 服务费 {{ formatFeeRate(serviceFeePreview.ratePercent) }} -{{ formatMoneyFixed(serviceFeePreview.fee) }} · 打手实际到账 <span class="font-semibold text-price">{{ formatMoneyFixed(serviceFeePreview.net) }}</span>
            </p>
          </div>
        </div>

        <!-- 炸单赔偿金：开关默认关，开启后填写金额（>0） -->
        <div class="sm:col-span-2 rounded-tile border border-line-1 p-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-semibold text-ink-1">炸单赔偿金</p>
              <p class="mt-1 text-xs leading-5 text-ink-3">打手接单时冻结，订单完结自动返还；炸单可在审核时扣除。</p>
            </div>
            <button
              type="button"
              :class="formData.compensation_enabled ? 'filter-pill-active' : 'filter-pill'"
              :aria-pressed="formData.compensation_enabled"
              @click="formData.compensation_enabled = !formData.compensation_enabled"
            >
              {{ formData.compensation_enabled ? '已开启' : '未开启' }}
            </button>
          </div>
          <div v-if="formData.compensation_enabled" class="mt-3">
            <label class="label" for="create-compensation">赔偿金额</label>
            <input id="create-compensation" v-model="formData.compensation_amount" type="number" min="0.01" step="0.01" class="input" placeholder="例如：50" />
          </div>
        </div>

        <div class="sm:col-span-2">
          <label class="label" for="create-attachments">图片附件（可选）</label>
          <input id="create-attachments" type="file" accept="image/png,image/jpeg,image/webp" multiple class="input min-h-[44px]" @change="formData.attachments = $event.target.files" />
          <p class="mt-1 text-xs text-ink-3">最多5张，支持 PNG、JPEG、WebP，单张不超过10MB。</p>
          <!-- 重建/续单带入的原单图片：走 payload.attachments 直接发布，不再重复上传 -->
          <p v-if="carriedAttachments().length" class="mt-1 text-xs text-ink-2">已带入原单 {{ carriedAttachments().length }} 张图片，可直接发布；重新选择图片会替换它们。</p>
          <p v-if="uploadProgress" class="mt-2 text-sm text-primary">图片上传中：{{ uploadProgress }}</p>
        </div>

        <div class="sm:col-span-2">
          <label class="label" for="create-notes">备注（可选）</label>
          <textarea id="create-notes" v-model="formData.notes" rows="3" class="input resize-none" placeholder="例如：希望晚上 8 点后开打，全程语音沟通。"></textarea>
        </div>
      </div>
    </section>

    <!-- 底部固定操作栏（sticky）：表单区照常滚动，取消/发布永远可见 -->
    <div ref="createActionsBar" class="create-actions-bar">
      <p class="hidden text-xs text-ink-3 sm:block">发布后金额进入托管，打手完结并经你审核后打款</p>
      <div class="flex w-full gap-3 sm:w-auto">
        <button type="button" class="btn-secondary flex-1 sm:flex-none" @click="router.push({ name: 'orders' })">取消</button>
        <button type="button" class="btn-primary flex-1 sm:flex-none" :disabled="isSubmitting || !canPublish" @click="publishOrder">
          {{ isSubmitting ? '正在发布...' : getPublishButtonLabel(formData.service_type) }}
        </button>
      </div>
    </div>
  </div>
</template>
