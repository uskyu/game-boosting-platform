<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useGamesStore } from '@/stores/games'
import { useOrdersStore } from '@/stores/orders'
import api from '@/utils/api'
import { getPublishButtonLabel } from '@/utils/humanCopy'

const router = useRouter()
const gamesStore = useGamesStore()
const ordersStore = useOrdersStore()

const errorMessage = ref('')
const successMessage = ref('')
const attachmentTypes = ['image/png', 'image/jpeg', 'image/webp']
const maxAttachmentCount = 5
const maxAttachmentSize = 10 * 1024 * 1024
const uploadProgress = ref('')

function validateAttachments(files) {
  const selected = Array.from(files || [])
  if (selected.length > maxAttachmentCount) return '订单最多上传5张图片'
  const invalid = selected.find((file) => !attachmentTypes.includes((file.type || '').toLowerCase()))
  if (invalid) return `仅支持 PNG、JPEG、WebP 图片：${invalid.name}`
  const oversized = selected.find((file) => file.size > maxAttachmentSize)
  if (oversized) return `单张图片不能超过10MB：${oversized.name}`
  return ''
}

async function uploadAttachments(orderId, files) {
  const selected = Array.from(files || [])
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
  server: '',
  role: '',
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
})

const payoutDelayOptions = [
  { value: '', label: '不设置' },
  { value: 1, label: '1天' },
  { value: 2, label: '2天' },
  { value: 3, label: '3天' },
  { value: 4, label: '4天' },
  { value: 5, label: '5天' },
]

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
    && formData.value.description_raw.trim()
    && Number(formData.value.price) > 0
  )
})

function handleGameChange() {
  formData.value.service_type = '陪玩'
  const game = selectedGame.value
  formData.value.game_name = game?.name || ''
}

async function publishOrder() {
  if (!canPublish.value) {
    errorMessage.value = '游戏、需求和价格都是必填项。'
    return
  }

  errorMessage.value = ''
  successMessage.value = ''
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

  const payoutDelay = formData.value.payout_delay_days === '' ? null : Number(formData.value.payout_delay_days)
  if (payoutDelay != null && (!Number.isInteger(payoutDelay) || payoutDelay < 1 || payoutDelay > 5)) {
    errorMessage.value = '到账时效需为 1-5 天'
    return
  }

  const maxClaims = Number(formData.value.max_claims)
  if (!Number.isInteger(maxClaims) || maxClaims < 1 || maxClaims > 100) {
    errorMessage.value = '最大接单人数需为 1-100 的整数'
    return
  }

  const title = formData.value.title.trim()
  const payload = {
    game_id: selectedGame.value.id,
    game_name: selectedGame.value.name,
    title: title || null,
    price: Number(formData.value.price),
    description_raw: formData.value.description_raw.trim(),
    service_type: '陪玩',
    server: formData.value.server.trim() || null,
    role: formData.value.role.trim() || null,
    notes: formData.value.notes.trim() || null,
    boss_contact: bossContact || null,
    max_claims: maxClaims,
    payout_delay_days: payoutDelay,
  }
  if (compensationAmount != null) {
    payload.compensation_amount = compensationAmount
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
  await gamesStore.ensureCatalog()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <p class="eyebrow">发布订单</p>
      <h1 class="section-title">填好需求，直接发布</h1>
      <p class="section-copy max-w-3xl">
        选游戏、写需求和价格，一键发布到大厅；打手接单后你去审核打款就行。
      </p>
      <div class="message-info mt-5 !mb-0">{{ escrowHint }}</div>
    </section>

    <div v-if="errorMessage" class="message-error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="message-success">{{ successMessage }}</div>

    <section class="surface-card p-6 sm:p-8">
      <div class="grid gap-5 sm:grid-cols-2">
        <div>
          <label class="label" for="create-game">游戏 *</label>
          <select id="create-game" v-model="formData.game_id" class="input" @change="handleGameChange">
            <option :value="null" disabled>请选择游戏</option>
            <option v-for="game in catalogGames" :key="game.id" :value="game.id">{{ game.name }}</option>
          </select>
          <p class="mt-1 text-xs text-ink-3">只能选择管理员上架的游戏</p>
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
          <label class="label" for="create-server">区服（可选）</label>
          <input id="create-server" v-model="formData.server" type="text" class="input" placeholder="例如：微信区 / 艾欧尼亚" />
        </div>
        <div>
          <label class="label" for="create-role">位置 / 角色偏好（可选）</label>
          <input id="create-role" v-model="formData.role" type="text" class="input" placeholder="例如：打野 / 中单" />
        </div>

        <div>
          <label class="label" for="create-boss-contact">老板ID（可选）</label>
          <input id="create-boss-contact" v-model="formData.boss_contact" type="text" class="input" maxlength="64" placeholder="接单后打手可见，用于添加你为好友" />
        </div>
        <div>
          <label class="label" for="create-payout-delay">到账时效</label>
          <select id="create-payout-delay" v-model="formData.payout_delay_days" class="input">
            <option v-for="option in payoutDelayOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
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
          <p v-if="uploadProgress" class="mt-2 text-sm text-primary">图片上传中：{{ uploadProgress }}</p>
        </div>

        <div class="sm:col-span-2">
          <label class="label" for="create-notes">备注（可选）</label>
          <textarea id="create-notes" v-model="formData.notes" rows="3" class="input resize-none" placeholder="例如：希望晚上 8 点后开打，全程语音沟通。"></textarea>
        </div>
      </div>

      <div class="mt-8 flex items-center justify-between gap-4">
        <p class="text-xs text-ink-3">发布后金额进入托管，打手完结并经你审核后打款</p>
        <button type="button" class="btn-primary shrink-0" :disabled="isSubmitting || !canPublish" @click="publishOrder">
          {{ isSubmitting ? '正在发布...' : getPublishButtonLabel(formData.service_type) }}
        </button>
      </div>
    </section>
  </div>
</template>
