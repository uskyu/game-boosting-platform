<script setup>
import { computed, onMounted, ref } from 'vue'

import { useWalletStore } from '@/stores/wallet'
import {
  TRANSACTION_TYPE_META,
  WITHDRAWAL_CHANNELS,
  getChannelLabel,
  getTransactionTypeLabel,
  getWithdrawalStatusLabel,
  getWithdrawalStatusTagClass,
} from '@/stores/wallet'
import { formatCount, formatDateTime, formatPrice } from '@/utils/display'

const walletStore = useWalletStore()

const withdrawForm = ref({ amount: '', channel: 'ALIPAY', account_name: '', account_no: '' })
const formErrors = ref({})
const withdrawMessage = ref({ type: '', text: '' })
const submittingWithdrawal = ref(false)

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
  const result = await walletStore.createWithdrawal({
    amount: Number(withdrawForm.value.amount),
    channel: withdrawForm.value.channel,
    account_name: withdrawForm.value.account_name.trim(),
    account_no: withdrawForm.value.account_no.trim(),
  })

  if (result.success) {
    withdrawMessage.value = { type: 'success', text: '提现申请已提交，请等待管理员审核打款' }
    withdrawForm.value = {
      amount: '',
      channel: withdrawForm.value.channel,
      account_name: withdrawForm.value.account_name,
      account_no: withdrawForm.value.account_no,
    }
    formErrors.value = {}
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
  refreshAll()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div class="space-y-3">
          <p class="eyebrow">资金中心</p>
          <h1 class="section-title">我的钱包</h1>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <article v-for="card in statCards" :key="card.label" :class="[card.cardClass, 'min-w-[150px]']">
            <p class="text-xs font-medium uppercase tracking-[0.16em] text-ink-2">{{ card.label }}</p>
            <p class="mt-2.5 text-2xl font-semibold tabular-nums" :class="card.valueClass">{{ card.value }}</p>
          </article>
        </div>
      </div>
    </section>

    <div class="wallet-grid">
    <section class="surface-card p-6 sm:p-8">
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

        <div class="lg:col-span-2">
          <button class="btn-primary w-full py-3 sm:w-auto sm:!px-10" :disabled="submittingWithdrawal || walletStore.submitting">
            {{ submittingWithdrawal ? '提交中...' : '提交申请' }}
          </button>
          <p class="helper-text">提交后金额将进入冻结状态，管理员审核通过并打款后完成提现。</p>
        </div>
      </form>
    </section>

    <section class="surface-card p-6 sm:p-8">
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
          class="info-tile flex flex-wrap items-center justify-between gap-x-6 gap-y-3 !p-4 transition-colors duration-base hover:bg-surface-3"
        >
          <div class="flex min-w-0 items-center gap-3">
            <span class="tag flex-none">{{ getTransactionTypeLabel(item.type) }}</span>
            <p class="truncate text-sm text-ink-2">{{ item.remark || '—' }}</p>
          </div>

          <div class="flex flex-wrap items-center gap-x-6 gap-y-2">
            <p class="text-xs text-ink-3">{{ formatDateTime(item.created_at) }}</p>
            <p class="w-32 text-right text-lg font-semibold" :class="transactionAmountClass(item)">
              {{ transactionAmountText(item) }}
            </p>
            <div class="min-w-[110px] text-right">
              <p class="info-tile__label">变动后余额</p>
              <p class="mt-1 text-sm font-medium tabular-nums text-ink-1">{{ formatPrice(item.balance_after) }}</p>
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

    <section class="surface-card p-6 sm:p-8">
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
  </div>
</template>
