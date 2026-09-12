<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { useOrdersStore } from '@/stores/orders'
import { useWalletStore } from '@/stores/wallet'
import {
  TRANSACTION_TYPE_META,
  WITHDRAWAL_CHANNELS,
  getChannelLabel,
  getTransactionTypeLabel,
  getWithdrawalStatusLabel,
  getWithdrawalStatusTagClass,
} from '@/stores/wallet'
import { getClaimSettlementMeta } from '@/utils/order'
import { formatCount, formatDateTime, formatOrderPrice, formatPrice, serverNow } from '@/utils/display'

const walletStore = useWalletStore()
const authStore = useAuthStore()
const ordersStore = useOrdersStore()

// 到账倒计时按秒跳动：now 用服务器校准时间（设备本地时钟可能偏差数秒）
const now = ref(serverNow())
let countdownTimer = null

// 交付后的订单按审核与自动结算拆分，避免把已审核记录继续显示为待审核。
const reviewClaims = computed(() => ordersStore.myClaims)
const pendingReviewClaims = computed(() => reviewClaims.value.filter((claim) => getClaimSettlementMeta(claim, now.value).isPendingReview))
const pendingSettlementClaims = computed(() => reviewClaims.value.filter((claim) => getClaimSettlementMeta(claim, now.value).isPendingSettlement))
const reviewLoading = computed(() => ordersStore.myClaimsLoading)

async function fetchReviewClaims() {
  if (authStore.isAdmin) return
  await ordersStore.fetchMyClaims('DELIVERED')
}

// ── 易支付充值 ──
const rechargeConfig = computed(() => walletStore.rechargeConfig)
const rechargeForm = ref({ amount: '', paymentMethod: '' })
const rechargeError = ref('')
const rechargeMessage = ref({ type: '', text: '' })
const submittingRecharge = ref(false)
const showRechargeModal = ref(false)

function openRecharge() {
  rechargeError.value = ''
  rechargeMessage.value = { type: '', text: '' }
  showRechargeModal.value = true
}

function closeRecharge() {
  if (submittingRecharge.value) return
  showRechargeModal.value = false
}

const RECHARGE_STATUS_META = {
  PENDING: { label: '待支付', tagClass: '!bg-warning-soft !text-warning' },
  SUCCESS: { label: '已到账', tagClass: '!bg-success-soft !text-success' },
  CLOSED: { label: '已关闭', tagClass: '!bg-surface-3 !text-ink-2' },
}

function getRechargeStatusMeta(status) {
  return RECHARGE_STATUS_META[status] || { label: status || '未知状态', tagClass: '!bg-surface-3 !text-ink-2' }
}

function getRechargeMethodLabel(type) {
  return rechargeConfig.value.pay_methods.find((item) => item.type === type)?.name || type || '-'
}

// 充值方式配置到位后默认选中第一项（避免按钮组空选）
watch(
  rechargeConfig,
  (config) => {
    const methods = config?.pay_methods || []
    if (methods.length && !methods.some((item) => item.type === rechargeForm.value.paymentMethod)) {
      rechargeForm.value.paymentMethod = methods[0].type
    }
  },
  { immediate: true }
)

// 易支付要求 POST 表单提交，用 params 构造隐藏表单并提交，把浏览器带到支付页
function submitPayForm(payUrl, params) {
  if (!payUrl || !params) return
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = payUrl
  form.style.display = 'none'
  Object.entries(params).forEach(([key, value]) => {
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = key
    input.value = String(value)
    form.appendChild(input)
  })
  document.body.appendChild(form)
  form.submit()
  document.body.removeChild(form)
}

function validateRechargeForm() {
  const amount = Number(rechargeForm.value.amount)
  const min = Number(rechargeConfig.value.min_amount)
  const minText = rechargeConfig.value.min_amount || '1.00'

  if (rechargeForm.value.amount === '' || !Number.isFinite(amount) || amount < min) {
    rechargeError.value = `充值金额不能低于 ${minText} 元`
    return false
  }
  if (!rechargeForm.value.paymentMethod) {
    rechargeError.value = '请选择支付方式'
    return false
  }
  rechargeError.value = ''
  return true
}

async function submitRecharge() {
  rechargeMessage.value = { type: '', text: '' }
  if (!validateRechargeForm()) {
    return
  }

  submittingRecharge.value = true
  const result = await walletStore.createRecharge(
    Number(rechargeForm.value.amount),
    rechargeForm.value.paymentMethod
  )
  submittingRecharge.value = false

  if (!result.success) {
    rechargeMessage.value = { type: 'error', text: result.error || '创建充值订单失败' }
    return
  }

  rechargeMessage.value = { type: 'success', text: '正在跳转到支付页面…' }
  showRechargeModal.value = false
  submitPayForm(result.data?.pay_url, result.data?.params)
  await Promise.all([walletStore.fetchMyRecharges({ page: 1 }), walletStore.fetchWallet()])
}

function handleRechargesPage(page) {
  if (page < 1 || page > myRechargesPagination.value.pages || page === myRechargesPagination.value.page) {
    return
  }
  walletStore.fetchMyRecharges({ page })
}

async function fetchRechargeData() {
  // 管理员同样可以充值（便于联调），因此不做角色区分
  await Promise.all([
    walletStore.fetchRechargeConfig(),
    walletStore.fetchMyRecharges({ page: 1 }),
  ])
}

// ── 保证金：可用余额转入保证金，按门槛匹配阶梯权益 ──
const depositOverview = computed(() => walletStore.depositOverview)
const depositForm = ref({ amount: '' })
const depositError = ref('')
const depositMessage = ref({ type: '', text: '' })
const submittingDeposit = ref(false)

// 保证金模式未开启但有余额时也要展示卡片，保证用户能把钱转回去
const showDepositCard = computed(
  () => Boolean(depositOverview.value?.enabled) || Number(depositOverview.value?.deposit_balance || 0) > 0
)

const depositWaitText = computed(() => {
  const seconds = depositOverview.value?.wait_seconds
  if (seconds == null) return '—'
  return Number(seconds) === 0 ? '立即可接' : `${seconds} 秒`
})

const depositSettleText = computed(() => {
  const hours = Number(depositOverview.value?.settle_hours)
  if (!Number.isFinite(hours) || hours < 0) return '—'
  if (hours === 0) return '立即结算'
  if (hours < 24) return `${hours} 小时`
  if (hours % 24 === 0) return `${hours / 24} 天`
  return `${hours} 小时`
})

const depositTierText = computed(() => {
  const threshold = depositOverview.value?.current_threshold
  return threshold == null ? '—' : `≥ ${formatPrice(threshold)}`
})

async function submitDepositTransfer() {
  depositMessage.value = { type: '', text: '' }
  const amount = Number(depositForm.value.amount)
  if (!depositForm.value.amount || !Number.isFinite(amount) || amount <= 0) {
    depositError.value = '请输入要转入的保证金金额'
    return
  }
  if (depositOverview.value && amount > Number(depositOverview.value.available_balance)) {
    depositError.value = '转入金额不能超过可用余额'
    return
  }
  depositError.value = ''
  submittingDeposit.value = true
  const result = await walletStore.transferToDeposit(amount)
  submittingDeposit.value = false
  if (!result.success) {
    depositMessage.value = { type: 'error', text: result.error || '转入保证金失败' }
    return
  }
  depositMessage.value = { type: 'success', text: '保证金已转入并冻结' }
  depositForm.value.amount = ''
  await Promise.all([walletStore.fetchDepositOverview(), walletStore.fetchWallet()])
}

async function fetchDepositData() {
  // 保证金只对打手开放，管理员接口返回 403
  if (authStore.isAdmin) return
  await walletStore.fetchDepositOverview()
}

const depositReturnForm = ref({ amount: '' })
const depositReturnError = ref('')
const submittingDepositReturn = ref(false)

const hasDepositBalance = computed(() => Number(depositOverview.value?.deposit_balance || 0) > 0)

async function submitDepositReturn() {
  depositMessage.value = { type: '', text: '' }
  const amount = Number(depositReturnForm.value.amount)
  if (!depositReturnForm.value.amount || !Number.isFinite(amount) || amount <= 0) {
    depositReturnError.value = '请输入要转回的保证金金额'
    return
  }
  if (amount > Number(depositOverview.value?.deposit_balance || 0)) {
    depositReturnError.value = '转回金额不能超过保证金余额'
    return
  }
  if (!depositOverview.value?.can_return) {
    depositReturnError.value = depositOverview.value?.return_block_reason || '当前不能转回余额'
    return
  }
  depositReturnError.value = ''
  submittingDepositReturn.value = true
  const result = await walletStore.transferFromDeposit(amount)
  submittingDepositReturn.value = false
  if (!result.success) {
    depositReturnError.value = result.error || '转回余额失败'
    return
  }
  depositMessage.value = { type: 'success', text: '保证金已转回可用余额' }
  depositReturnForm.value.amount = ''
  await Promise.all([walletStore.fetchDepositOverview(), walletStore.fetchWallet()])
}

const withdrawForm = ref({ amount: '', channel: 'ALIPAY', account_name: '', account_no: '' })
const formErrors = ref({})
const withdrawMessage = ref({ type: '', text: '' })
const submittingWithdrawal = ref(false)

// ── 收款二维码（ALIPAY / WECHAT 可选，PNG/JPEG/WebP ≤10MB）──
const qrcode = ref(null) // 上传成功后 {url,name,size,content_type}
const qrcodeUploading = ref(false)
const qrcodeError = ref('')
const QRCODE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const QRCODE_MAX_SIZE = 10 * 1024 * 1024

// BANK 渠道隐藏上传控件；切换渠道保留已传二维码，仅按渠道显隐
const showQrcodeUpload = computed(() => ['ALIPAY', 'WECHAT'].includes(withdrawForm.value.channel))

async function handleQrcodeChange(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  qrcodeError.value = ''
  if (!QRCODE_TYPES.includes((file.type || '').toLowerCase())) {
    qrcodeError.value = '二维码仅支持 PNG、JPEG、WebP 图片'
    return
  }
  if (file.size > QRCODE_MAX_SIZE) {
    qrcodeError.value = '二维码图片不能超过 10MB'
    return
  }
  qrcodeUploading.value = true
  const result = await walletStore.uploadWithdrawalQrcode(file)
  qrcodeUploading.value = false
  if (result.success) {
    qrcode.value = result.data
  } else {
    qrcodeError.value = result.error || '二维码上传失败'
  }
}

function removeQrcode() {
  qrcode.value = null
  qrcodeError.value = ''
}

const walletInfo = computed(() =>
  walletStore.wallet || { available_balance: 0, frozen_balance: 0, total_income: 0, total_withdrawn: 0 }
)

// 统计卡＝白卡同款，仅数字颜色区分语义（文档 5 节：可用余额/提现=ink-1、冻结=warning、收入=success）
const statCards = computed(() => [
  { label: '可用余额', value: formatPrice(walletInfo.value.available_balance), valueClass: 'text-ink-1', cardClass: 'stat-card' },
  { label: '冻结金额', value: formatPrice(walletInfo.value.frozen_balance), valueClass: 'text-warning', cardClass: 'stat-card' },
  { label: '累计收入', value: formatPrice(walletInfo.value.total_income), valueClass: 'text-success', cardClass: 'stat-card' },
  { label: '累计提现', value: formatPrice(walletInfo.value.total_withdrawn), valueClass: 'text-ink-1', cardClass: 'stat-card' },
])

const transactions = computed(() => walletStore.transactions)
const transactionsPagination = computed(() => walletStore.transactionsPagination)
const myWithdrawals = computed(() => walletStore.myWithdrawals)
const myWithdrawalsPagination = computed(() => walletStore.myWithdrawalsPagination)
const myRecharges = computed(() => walletStore.myRecharges)
const myRechargesPagination = computed(() => walletStore.myRechargesPagination)

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

function transactionDirection(item) {
  const meta = TRANSACTION_TYPE_META[item.type]
  const amount = Number(item.amount ?? 0)
  if (!meta || meta.direction === 'auto') {
    return amount >= 0 ? 'in' : 'out'
  }
  return meta.direction
}

function transactionAmountText(item) {
  const amount = Math.abs(Number(item.amount ?? 0))
  return `${transactionDirection(item) === 'in' ? '+' : '-'}${formatPrice(amount)}`
}

function transactionAmountClass(item) {
  return transactionDirection(item) === 'in' ? 'text-success' : 'text-danger'
}

function validateWithdrawForm() {
  const errors = {}
  const amount = Number(withdrawForm.value.amount)

  if (withdrawForm.value.amount === '' || !Number.isFinite(amount) || amount < 1) {
    errors.amount = '提现金额不能低于 1 元'
  } else if (walletStore.wallet && amount > walletInfo.value.available_balance) {
    errors.amount = '提现金额不能超过可用余额'
  }

  if (!withdrawForm.value.account_name.trim()) {
    errors.account_name = '请填写收款人姓名'
  }

  if (!withdrawForm.value.account_no.trim()) {
    errors.account_no = '请填写收款账号'
  }

  formErrors.value = errors
  return Object.keys(errors).length === 0
}

async function submitWithdrawal() {
  withdrawMessage.value = { type: '', text: '' }
  if (!validateWithdrawForm()) {
    return
  }

  submittingWithdrawal.value = true
  const payload = {
    amount: Number(withdrawForm.value.amount),
    channel: withdrawForm.value.channel,
    account_name: withdrawForm.value.account_name.trim(),
    account_no: withdrawForm.value.account_no.trim(),
  }
  if (qrcode.value?.url) {
    payload.qrcode_url = qrcode.value.url
  }
  const result = await walletStore.createWithdrawal(payload)

  if (result.success) {
    withdrawMessage.value = { type: 'success', text: '提现申请已提交，请等待管理员审核打款' }
    withdrawForm.value = {
      amount: '',
      channel: withdrawForm.value.channel,
      account_name: withdrawForm.value.account_name,
      account_no: withdrawForm.value.account_no,
    }
    formErrors.value = {}
    removeQrcode()
    await Promise.all([
      walletStore.fetchWallet(),
      walletStore.fetchMyWithdrawals({ page: 1 }),
      walletStore.fetchTransactions({ page: 1 }),
    ])
  } else {
    withdrawMessage.value = { type: 'error', text: result.error || '提交失败' }
  }
  submittingWithdrawal.value = false
}

function handleTransactionsPage(page) {
  if (page < 1 || page > transactionsPagination.value.pages || page === transactionsPagination.value.page) {
    return
  }
  walletStore.fetchTransactions({ page })
}

function handleWithdrawalsPage(page) {
  if (page < 1 || page > myWithdrawalsPagination.value.pages || page === myWithdrawalsPagination.value.page) {
    return
  }
  walletStore.fetchMyWithdrawals({ page })
}

async function refreshAll() {
  await Promise.all([
    walletStore.fetchWallet(),
    walletStore.fetchTransactions({ page: 1 }),
    walletStore.fetchMyWithdrawals({ page: 1 }),
  ])
}

onMounted(() => {
  // 并行拉取：钱包数据与审核中报名单互不依赖，串行会放大远程库延迟
  Promise.all([refreshAll(), fetchReviewClaims(), fetchRechargeData(), fetchDepositData()])
  countdownTimer = window.setInterval(() => {
    now.value = serverNow()
  }, 1000)
})

onUnmounted(() => {
  if (countdownTimer) window.clearInterval(countdownTimer)
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-5 sm:p-6 lg:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div class="space-y-3">
          <p class="eyebrow">资金中心</p>
          <h1 class="section-title">我的钱包</h1>
          <button
            v-if="rechargeConfig.enabled"
            type="button"
            class="btn-primary min-h-[44px] !px-6"
            @click="openRecharge"
          >
            充值
          </button>
        </div>

        <!-- 手机 2×2 统计卡，桌面端一行四张 -->
        <div class="grid w-full grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
          <article v-for="card in statCards" :key="card.label" :class="card.cardClass">
            <p class="text-xs font-medium uppercase tracking-[0.16em] text-ink-2">{{ card.label }}</p>
            <p class="mt-2 text-xl font-semibold tabular-nums sm:mt-2.5 sm:text-2xl" :class="card.valueClass">{{ card.value }}</p>
          </article>
        </div>
      </div>
    </section>

    <!-- 审核中订单：已交付汇报、等待订单发布人审核打款的接单记录 -->
    <section v-if="!authStore.isAdmin" class="surface-card p-4 sm:p-6 lg:p-8">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="text-lg font-semibold text-ink-1">交付结算中的订单</h2>
        <div class="flex flex-wrap justify-end gap-2 text-xs font-semibold tabular-nums">
          <span v-if="pendingReviewClaims.length" class="tag !bg-warning-soft !text-warning">{{ pendingReviewClaims.length }} 单待审核</span>
          <span v-if="pendingSettlementClaims.length" class="tag !bg-info-soft !text-info">{{ pendingSettlementClaims.length }} 单待自动结算</span>
        </div>
      </div>
      <p class="mt-1 text-sm text-ink-3">已提交结束汇报的记录会在这里显示；审核通过后按结算时间自动入账。</p>

      <div v-if="reviewLoading" class="mt-4 space-y-2" aria-busy="true">
        <div v-for="n in 2" :key="`review-skeleton-${n}`" class="skeleton h-14 !rounded-tile"></div>
      </div>
      <div v-else-if="!reviewClaims.length" class="empty-state mt-4">
        <div class="empty-state__icon" aria-hidden="true">📭</div>
        <h4 class="empty-state__title">暂无交付结算中的订单</h4>
        <p class="empty-state__copy">完成订单并提交汇报后，会在这里查看审核和自动结算进度。</p>
      </div>
      <ul v-else class="mt-4 space-y-2">
        <li v-for="claim in reviewClaims" :key="claim.id" class="claims-item">
          <router-link :to="{ name: 'order-detail', params: { id: claim.order?.id || claim.order_id } }" class="flex items-center justify-between gap-3">
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-ink-1">{{ claim.order?.title || claim.order?.game_name || '代练订单' }}</p>
              <p class="mt-1 text-xs text-ink-3">
                订单 #{{ claim.order?.id || claim.order_id }} · 接单记录 #{{ claim.id }} · {{ claim.order?.game_name || '' }}<template v-if="claim.delivered_at"> · 交付于 {{ formatDateTime(claim.delivered_at) }}</template>
              </p>
            </div>
            <div class="shrink-0 text-right">
              <p class="text-sm font-semibold tabular-nums text-price">{{ claim.order ? formatOrderPrice(claim.order) : formatPrice(0) }}</p>
              <p :class="['mt-0.5 text-xs', getClaimSettlementMeta(claim, now.value).tagClass]">{{ getClaimSettlementMeta(claim, now.value).label }}</p>
              <p v-if="getClaimSettlementMeta(claim, now.value).isPendingSettlement && claim.settlement_due_at" class="mt-1 text-xs text-ink-3">
                预计 {{ formatDateTime(claim.settlement_due_at) }} 自动结算
              </p>
              <p v-if="getClaimSettlementMeta(claim, now.value).countdown" class="mt-1 text-xs text-ink-3">
                {{ formatDateTime(claim.settlement_due_at) }} 自动入账
              </p>
            </div>
          </router-link>
        </li>
      </ul>
    </section>

    <div class="wallet-grid">
    <!-- 保证金：可用余额转入保证金，按门槛匹配阶梯权益 -->
    <section v-if="!authStore.isAdmin && showDepositCard" class="surface-card p-4 sm:p-6 lg:p-8">
      <div class="flex items-center justify-between gap-4">
        <h2 class="text-2xl font-semibold text-ink-1">保证金</h2>
        <router-link :to="{ name: 'deposit' }" class="btn-ghost !px-4 !py-2 text-sm">查看权益阶梯</router-link>
      </div>

      <div class="mt-6 grid gap-4 sm:grid-cols-3">
        <article class="info-tile !p-4">
          <p class="text-xs uppercase tracking-[0.16em] text-ink-2">保证金余额</p>
          <p class="mt-2 text-xl font-semibold tabular-nums text-ink-1">{{ formatPrice(depositOverview?.deposit_balance) }}</p>
        </article>
        <article class="info-tile !p-4">
          <p class="text-xs uppercase tracking-[0.16em] text-ink-2">当前档位</p>
          <p class="mt-2 text-xl font-semibold tabular-nums text-ink-1">{{ depositTierText }}</p>
        </article>
        <article class="info-tile !p-4">
          <p class="text-xs uppercase tracking-[0.16em] text-ink-2">可用余额</p>
          <p class="mt-2 text-xl font-semibold tabular-nums text-ink-1">{{ formatPrice(depositOverview?.available_balance) }}</p>
        </article>
      </div>

      <p class="mt-4 text-sm text-ink-2">
        当前档位权益：接单等待 <span class="font-semibold text-ink-1">{{ depositWaitText }}</span>
        · 接单免冻结赔付金 <span class="font-semibold text-ink-1">{{ depositOverview?.exempt_compensation ? '是' : '否' }}</span>
        · 结账时效 <span class="font-semibold text-ink-1">{{ depositSettleText }}</span>
      </p>

      <div v-if="depositMessage.text" class="mt-4" :class="messageClass(depositMessage.type)">
        {{ depositMessage.text }}
      </div>

      <div class="mt-6 grid max-w-3xl gap-6 sm:grid-cols-2">
        <form v-if="depositOverview?.enabled" class="grid content-start gap-3" @submit.prevent="submitDepositTransfer">
          <div>
            <label class="label" for="deposit-amount">转入保证金（元）</label>
            <input
              id="deposit-amount"
              v-model="depositForm.amount"
              type="number"
              min="0.01"
              step="0.01"
              class="input min-h-[44px]"
              :class="{ 'input-error': depositError }"
              placeholder="例如 100"
            />
            <p v-if="depositError" class="mt-2 text-xs text-danger">{{ depositError }}</p>
            <p class="helper-text">从可用余额转入，转入后即冻结。</p>
          </div>
          <button
            class="btn-primary min-h-[44px]"
            :disabled="submittingDeposit || walletStore.depositSubmitting"
          >
            {{ submittingDeposit ? '转入中...' : '转入保证金' }}
          </button>
        </form>
        <p v-else class="text-sm text-ink-3">保证金功能未开启，暂时不能缴纳。</p>

        <form v-if="hasDepositBalance" class="grid content-start gap-3" @submit.prevent="submitDepositReturn">
          <div>
            <label class="label" for="deposit-return-amount">转回余额（元）</label>
            <input
              id="deposit-return-amount"
              v-model="depositReturnForm.amount"
              type="number"
              min="0.01"
              step="0.01"
              class="input min-h-[44px]"
              :class="{ 'input-error': depositReturnError }"
              :disabled="!depositOverview?.can_return"
              placeholder="例如 100"
            />
            <p v-if="depositReturnError" class="mt-2 text-xs text-danger">{{ depositReturnError }}</p>
            <p v-if="!depositOverview?.can_return && depositOverview?.return_block_reason" class="mt-2 text-xs text-warning">
              {{ depositOverview.return_block_reason }}
            </p>
            <p v-else class="helper-text">转回后保证金减少，档位权益会随之变化。</p>
          </div>
          <button
            class="btn-secondary min-h-[44px]"
            :disabled="!depositOverview?.can_return || submittingDepositReturn || walletStore.depositSubmitting"
          >
            {{ submittingDepositReturn ? '转回中...' : '转回余额' }}
          </button>
        </form>
      </div>
    </section>

    <section v-if="rechargeConfig.enabled || myRecharges.length" class="surface-card p-4 sm:p-6 lg:p-8">
      <h2 class="text-2xl font-semibold text-ink-1">我的充值记录</h2>

      <div v-if="walletStore.myRechargesLoading" class="mt-6 space-y-3" aria-busy="true">
        <div v-for="n in 3" :key="`rc-skeleton-${n}`" class="info-tile flex items-center justify-between gap-4">
          <div class="skeleton-line h-4 w-40"></div>
          <div class="skeleton-line h-6 w-20"></div>
        </div>
      </div>

      <div v-else-if="!myRecharges.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">💳</div>
        <h3 class="empty-state__title">暂无充值记录</h3>
        <p class="empty-state__copy">充值订单创建后，支付状态会显示在这里。</p>
      </div>

      <div v-else class="mt-6 space-y-3">
        <article
          v-for="item in myRecharges"
          :key="item.id"
          class="info-tile !p-5 transition-colors duration-base hover:bg-surface-3"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="space-y-2">
              <p class="text-xl font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</p>
              <p class="text-sm text-ink-2">
                {{ getRechargeMethodLabel(item.payment_method) }} · 单号 {{ item.trade_no || '-' }}
              </p>
              <p class="text-xs text-ink-3">创建于 {{ formatDateTime(item.created_at) }}</p>
            </div>

            <div class="flex flex-col items-end gap-2">
              <span :class="['tag', getRechargeStatusMeta(item.status).tagClass]">
                {{ getRechargeStatusMeta(item.status).label }}
              </span>
              <p v-if="item.paid_at" class="text-xs text-ink-3">到账于 {{ formatDateTime(item.paid_at) }}</p>
            </div>
          </div>
        </article>
      </div>

      <div v-if="myRechargesPagination.pages > 1" class="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm text-ink-2">
          {{ myRechargesPagination.page }} / {{ myRechargesPagination.pages }} · {{ formatCount(myRechargesPagination.total) }} 条
        </p>
        <div class="flex items-center gap-2">
          <button class="btn-secondary !px-4 !py-2" :disabled="myRechargesPagination.page <= 1" @click="handleRechargesPage(myRechargesPagination.page - 1)">上一页</button>
          <button class="btn-secondary !px-4 !py-2" :disabled="myRechargesPagination.page >= myRechargesPagination.pages" @click="handleRechargesPage(myRechargesPagination.page + 1)">下一页</button>
        </div>
      </div>
    </section>

    <section class="surface-card p-4 sm:p-6 lg:p-8">
      <div class="flex items-center justify-between gap-4">
        <h2 class="text-2xl font-semibold text-ink-1">申请提现</h2>
        <button class="btn-secondary !px-4 !py-2" :disabled="walletStore.walletLoading" @click="refreshAll">
          刷新
        </button>
      </div>

      <div v-if="withdrawMessage.text" class="mt-4" :class="messageClass(withdrawMessage.type)">
        {{ withdrawMessage.text }}
      </div>

      <form class="mt-6 grid gap-5 lg:grid-cols-2" @submit.prevent="submitWithdrawal">
        <div>
          <label class="label" for="withdraw-amount">提现金额（元）</label>
          <input
            id="withdraw-amount"
            v-model="withdrawForm.amount"
            type="number"
            min="1"
            step="0.01"
            class="input"
            :class="{ 'input-error': formErrors.amount }"
            placeholder="最低 1 元"
          />
          <p v-if="formErrors.amount" class="mt-2 text-xs text-danger">{{ formErrors.amount }}</p>
        </div>

        <div>
          <label class="label" for="withdraw-channel">收款渠道</label>
          <select id="withdraw-channel" v-model="withdrawForm.channel" class="input">
            <option v-for="channel in WITHDRAWAL_CHANNELS" :key="channel.value" :value="channel.value">
              {{ channel.label }}
            </option>
          </select>
        </div>

        <div>
          <label class="label" for="withdraw-account-name">收款人姓名</label>
          <input
            id="withdraw-account-name"
            v-model="withdrawForm.account_name"
            type="text"
            class="input"
            :class="{ 'input-error': formErrors.account_name }"
            placeholder="请填写实名收款人"
          />
          <p v-if="formErrors.account_name" class="mt-2 text-xs text-danger">{{ formErrors.account_name }}</p>
        </div>

        <div>
          <label class="label" for="withdraw-account-no">收款账号</label>
          <input
            id="withdraw-account-no"
            v-model="withdrawForm.account_no"
            type="text"
            class="input"
            :class="{ 'input-error': formErrors.account_no }"
            :placeholder="withdrawForm.channel === 'BANK' ? '银行卡号' : '支付宝 / 微信账号'"
          />
          <p v-if="formErrors.account_no" class="mt-2 text-xs text-danger">{{ formErrors.account_no }}</p>
        </div>

        <!-- 收款二维码：仅支付宝 / 微信渠道显示（切换渠道保留已传图，仅按渠道显隐） -->
        <div v-if="showQrcodeUpload" class="lg:col-span-2">
          <label class="label" for="withdraw-qrcode">收款二维码（可选）</label>
          <div v-if="qrcode" class="flex flex-wrap items-center gap-4">
            <img
              :src="qrcode.url"
              alt="收款二维码"
              class="h-24 w-24 rounded-tile border border-line-1 bg-surface-2 object-cover"
            />
            <button type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" :disabled="qrcodeUploading" @click="removeQrcode">
              删除二维码
            </button>
          </div>
          <input
            v-else
            id="withdraw-qrcode"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            class="input min-h-[44px]"
            :disabled="qrcodeUploading"
            @change="handleQrcodeChange"
          />
          <p class="mt-1.5 text-xs text-ink-3">支持 PNG、JPEG、WebP，不超过 10MB；上传后管理员打款时可直接扫码。</p>
          <p v-if="qrcodeUploading" class="mt-2 text-sm text-primary">二维码上传中…</p>
          <p v-if="qrcodeError" class="mt-2 text-xs text-danger">{{ qrcodeError }}</p>
        </div>

        <div class="lg:col-span-2">
          <button class="btn-primary w-full py-3 sm:w-auto sm:!px-10" :disabled="submittingWithdrawal || walletStore.submitting">
            {{ submittingWithdrawal ? '提交中...' : '提交申请' }}
          </button>
          <p class="helper-text">提交后金额将进入冻结状态，管理员审核通过并打款后完成提现。</p>
        </div>
      </form>
    </section>

    <section class="surface-card p-4 sm:p-6 lg:p-8">
      <div class="flex items-center justify-between gap-4">
        <h2 class="text-2xl font-semibold text-ink-1">资金流水</h2>
        <button class="btn-ghost !px-4 !py-2 text-sm" :disabled="walletStore.transactionsLoading" @click="refreshAll">
          刷新
        </button>
      </div>

      <div v-if="walletStore.transactionsLoading" class="mt-6 space-y-3" aria-busy="true">
        <div v-for="n in 4" :key="`tx-skeleton-${n}`" class="info-tile flex items-center justify-between gap-4">
          <div class="skeleton-line h-4 w-32"></div>
          <div class="skeleton-line h-4 w-24"></div>
        </div>
      </div>

      <div v-else-if="!transactions.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">🧾</div>
        <h3 class="empty-state__title">暂无流水记录</h3>
        <p class="empty-state__copy">接单收入、提现和调账都会记录在这里。</p>
      </div>

      <div v-else class="mt-6 space-y-3">
        <article
          v-for="item in transactions"
          :key="item.id"
          class="info-tile flex flex-col gap-2.5 !p-4 transition-colors duration-base hover:bg-surface-3 sm:flex-row sm:items-center sm:justify-between sm:gap-x-6"
        >
          <div class="flex min-w-0 items-center gap-3">
            <span class="tag flex-none">{{ getTransactionTypeLabel(item.type) }}</span>
            <p class="truncate text-sm text-ink-2">{{ item.remark || '—' }}</p>
          </div>

          <!-- 手机：时间靠左、金额+变动后余额靠右一行；桌面：三段横排 -->
          <div class="flex items-end justify-between gap-3 sm:items-center sm:justify-end sm:gap-x-6">
            <p class="text-xs text-ink-3">{{ formatDateTime(item.created_at) }}</p>
            <div class="text-right">
              <p class="text-lg font-semibold" :class="transactionAmountClass(item)">
                {{ transactionAmountText(item) }}
              </p>
              <p class="mt-0.5 text-xs text-ink-3">变动后 {{ formatPrice(item.balance_after) }}</p>
            </div>
          </div>
        </article>
      </div>

      <div v-if="transactionsPagination.pages > 1" class="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm text-ink-2">
          {{ transactionsPagination.page }} / {{ transactionsPagination.pages }} · {{ formatCount(transactionsPagination.total) }} 条
        </p>
        <div class="flex items-center gap-2">
          <button class="btn-secondary !px-4 !py-2" :disabled="transactionsPagination.page <= 1" @click="handleTransactionsPage(transactionsPagination.page - 1)">上一页</button>
          <button class="btn-secondary !px-4 !py-2" :disabled="transactionsPagination.page >= transactionsPagination.pages" @click="handleTransactionsPage(transactionsPagination.page + 1)">下一页</button>
        </div>
      </div>
    </section>

    <section class="surface-card p-4 sm:p-6 lg:p-8">
      <h2 class="text-2xl font-semibold text-ink-1">我的提现记录</h2>

      <div v-if="walletStore.myWithdrawalsLoading" class="mt-6 space-y-3" aria-busy="true">
        <div v-for="n in 3" :key="`wd-skeleton-${n}`" class="info-tile flex items-center justify-between gap-4">
          <div class="skeleton-line h-4 w-40"></div>
          <div class="skeleton-line h-6 w-20"></div>
        </div>
      </div>

      <div v-else-if="!myWithdrawals.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">💸</div>
        <h3 class="empty-state__title">暂无提现记录</h3>
        <p class="empty-state__copy">提交提现申请后，审核进度会显示在这里。</p>
      </div>

      <div v-else class="mt-6 space-y-3">
        <article
          v-for="item in myWithdrawals"
          :key="item.id"
          class="info-tile !p-5 transition-colors duration-base hover:bg-surface-3"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="space-y-2">
              <p class="text-xl font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</p>
              <p class="text-sm text-ink-2">
                {{ getChannelLabel(item.channel) }} · {{ item.account_name || '-' }} · {{ item.account_no || '-' }}
              </p>
              <p class="text-xs text-ink-3">申请于 {{ formatDateTime(item.created_at) }}</p>
            </div>

            <div class="flex flex-col items-end gap-2">
              <span :class="['tag', getWithdrawalStatusTagClass(item.status)]">
                {{ getWithdrawalStatusLabel(item.status) }}
              </span>
              <p v-if="item.paid_at" class="text-xs text-ink-3">打款于 {{ formatDateTime(item.paid_at) }}</p>
            </div>
          </div>

          <div
            v-if="item.status === 'REJECTED' && item.reject_reason"
            class="message-error mt-4"
          >
            驳回原因：{{ item.reject_reason }}
          </div>

          <div
            v-if="item.payment_reference && ['APPROVED', 'PAID'].includes(item.status)"
            class="message-info mt-4"
          >
            打款流水号：{{ item.payment_reference }}
          </div>
        </article>
      </div>

      <div v-if="myWithdrawalsPagination.pages > 1" class="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm text-ink-2">
          {{ myWithdrawalsPagination.page }} / {{ myWithdrawalsPagination.pages }} · {{ formatCount(myWithdrawalsPagination.total) }} 条
        </p>
        <div class="flex items-center gap-2">
          <button class="btn-secondary !px-4 !py-2" :disabled="myWithdrawalsPagination.page <= 1" @click="handleWithdrawalsPage(myWithdrawalsPagination.page - 1)">上一页</button>
          <button class="btn-secondary !px-4 !py-2" :disabled="myWithdrawalsPagination.page >= myWithdrawalsPagination.pages" @click="handleWithdrawalsPage(myWithdrawalsPagination.page + 1)">下一页</button>
        </div>
      </div>
    </section>
    </div>

    <!-- 充值弹窗：点「充值」按钮后在这里填写金额与支付方式 -->
    <teleport to="body">
      <div v-if="showRechargeModal" class="modal-scrim modal-scrim--sheet" @click.self="closeRecharge">
        <div class="modal-card modal-sheet !max-w-[440px]" role="dialog" aria-modal="true" aria-label="充值">
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-lg font-semibold text-ink-1">充值</h3>
            <button type="button" class="btn-ghost !min-h-[44px] !px-3" :disabled="submittingRecharge" @click="closeRecharge">关闭</button>
          </div>

          <p class="mt-1 text-sm text-ink-3">
            当前余额 {{ formatPrice(walletInfo.available_balance) }}，充值后自动计入可用余额。
          </p>

          <div v-if="rechargeMessage.text" class="mt-4" :class="messageClass(rechargeMessage.type)">
            {{ rechargeMessage.text }}
          </div>

          <form class="mt-5 grid gap-5" @submit.prevent="submitRecharge">
            <div>
              <label class="label" for="recharge-amount">充值金额（元）</label>
              <input
                id="recharge-amount"
                v-model="rechargeForm.amount"
                type="number"
                :min="rechargeConfig.min_amount"
                step="0.01"
                class="input min-h-[44px]"
                :class="{ 'input-error': rechargeError }"
                :placeholder="`最低 ${rechargeConfig.min_amount} 元`"
              />
            </div>

            <div>
              <label class="label" for="recharge-method">支付方式</label>
              <select id="recharge-method" v-model="rechargeForm.paymentMethod" class="input min-h-[44px]">
                <option v-for="method in rechargeConfig.pay_methods" :key="method.type" :value="method.type">
                  {{ method.name }}
                </option>
              </select>
            </div>

            <div>
              <p v-if="rechargeError" class="mb-3 text-xs text-danger">{{ rechargeError }}</p>
              <button
                class="btn-primary w-full py-3"
                :disabled="submittingRecharge || walletStore.submitting || !rechargeConfig.pay_methods.length"
              >
                {{ submittingRecharge ? '提交中...' : '立即充值' }}
              </button>
              <p class="helper-text">提交后跳转到支付页面完成付款，到账后金额自动计入余额。</p>
            </div>
          </form>
        </div>
      </div>
    </teleport>
  </div>
</template>

<style scoped>
/* 审核中订单列表项（与运营台同款卡片样式） */
.claims-item {
  border-radius: 14px;
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  padding: 12px;
}
</style>
