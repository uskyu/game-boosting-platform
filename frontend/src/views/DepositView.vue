<script setup>
/**
 * 我的保证金。
 * 顶部概览（保证金余额 / 可用余额 / 当前档位权益）→ 缴纳保证金（从余额转入）
 * → 转回余额 → 权益阶梯表（当前档位高亮）。
 * enabled=false 时隐藏缴纳入口，但 deposit_balance>0 仍可转回。
 */
import { computed, onMounted, ref } from 'vue'

import { useWalletStore } from '@/stores/wallet'
import { formatPrice } from '@/utils/display'

const walletStore = useWalletStore()

const overview = computed(() => walletStore.depositOverview)
const loading = computed(() => walletStore.depositOverviewLoading)
const loadError = ref('')

const inForm = ref({ amount: '' })
const outForm = ref({ amount: '' })
const inMessage = ref({ type: '', text: '' })
const outMessage = ref({ type: '', text: '' })
const submittingIn = ref(false)
const submittingOut = ref(false)

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

// 结账时效展示：<24 小时显示「X 小时」；能被 24 整除显示「X 天」；其余显示「X 小时」
function formatSettleHours(hours) {
  const value = Number(hours)
  if (!Number.isFinite(value) || value <= 0) return '-'
  if (value < 24) return `${value} 小时`
  if (value % 24 === 0) return `${value / 24} 天`
  return `${value} 小时`
}

function formatWaitSeconds(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value <= 0) return '立即可接'
  return `等待 ${value} 秒`
}

// 当前档位高亮：用 current_threshold 与各档门槛比对（可能为 null）
function isCurrentTier(tier) {
  const threshold = overview.value?.current_threshold
  if (threshold == null) return false
  return Number(tier?.threshold) === Number(threshold)
}

const canReturn = computed(() => Boolean(overview.value?.can_return))

function validateAmount(raw, max, noun) {
  const amount = Number(raw)
  if (raw === '' || raw === null || !Number.isFinite(amount) || amount <= 0) {
    return `${noun}金额需大于 0`
  }
  if (max != null && amount > Number(max)) {
    return `${noun}金额不能超过${noun === '转入' ? '可用余额' : '保证金余额'}`
  }
  return ''
}

async function submitIn() {
  inMessage.value = { type: '', text: '' }
  const error = validateAmount(inForm.value.amount, overview.value?.available_balance, '转入')
  if (error) {
    inMessage.value = { type: 'error', text: error }
    return
  }

  submittingIn.value = true
  const result = await walletStore.transferToDeposit(Number(inForm.value.amount))
  submittingIn.value = false

  if (result.success) {
    inMessage.value = { type: 'success', text: result.data?.message || '保证金缴纳成功' }
    inForm.value.amount = ''
    await Promise.all([walletStore.fetchDepositOverview(), walletStore.fetchWallet()])
  } else {
    inMessage.value = { type: 'error', text: result.error || '缴纳失败' }
  }
}

async function submitOut() {
  outMessage.value = { type: '', text: '' }
  if (!canReturn.value) {
    outMessage.value = { type: 'error', text: overview.value?.return_block_reason || '当前不可转回余额' }
    return
  }
  const error = validateAmount(outForm.value.amount, overview.value?.deposit_balance, '转回')
  if (error) {
    outMessage.value = { type: 'error', text: error }
    return
  }

  submittingOut.value = true
  const result = await walletStore.transferFromDeposit(Number(outForm.value.amount))
  submittingOut.value = false

  if (result.success) {
    outMessage.value = { type: 'success', text: result.data?.message || '已转回余额' }
    outForm.value.amount = ''
    await Promise.all([walletStore.fetchDepositOverview(), walletStore.fetchWallet()])
  } else {
    outMessage.value = { type: 'error', text: result.error || '转回失败' }
  }
}

onMounted(async () => {
  const result = await walletStore.fetchDepositOverview()
  if (!result.success) {
    loadError.value = result.error || '加载保证金信息失败'
  }
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <p class="eyebrow">资金中心</p>
      <h1 class="section-title">我的保证金</h1>
      <p class="mt-2 text-sm text-ink-2">保证金余额越充足，接单权益越高：优先接单（免等待）、接单不预冻结炸单赔付金、更快的结账时效。</p>

      <div v-if="overview" class="mt-6 grid gap-3 sm:gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <article class="stat-card">
          <p class="text-xs font-medium uppercase tracking-[0.12em] text-ink-2">保证金余额</p>
          <p class="mt-2.5 text-2xl font-semibold tabular-nums text-ink-1">{{ formatPrice(overview.deposit_balance) }}</p>
        </article>
        <article class="stat-card">
          <p class="text-xs font-medium uppercase tracking-[0.12em] text-ink-2">可用余额</p>
          <p class="mt-2.5 text-2xl font-semibold tabular-nums text-ink-1">{{ formatPrice(overview.available_balance) }}</p>
        </article>
        <article class="stat-card xl:col-span-2">
          <p class="text-xs font-medium uppercase tracking-[0.12em] text-ink-2">当前档位权益</p>
          <p class="mt-2.5 text-sm text-ink-1">
            接单等待 {{ formatWaitSeconds(overview.wait_seconds) }}
            <span class="mx-2 text-ink-3">·</span>
            接单免冻结赔付金 {{ overview.exempt_compensation ? '是' : '否' }}
            <span class="mx-2 text-ink-3">·</span>
            结账时效 {{ formatSettleHours(overview.settle_hours) }}
          </p>
        </article>
      </div>
    </section>

    <div v-if="loading && !overview" class="space-y-6" aria-busy="true">
      <div class="skeleton h-40 !rounded-card"></div>
      <div class="skeleton h-64 !rounded-card"></div>
    </div>

    <template v-else-if="overview">
      <div class="grid gap-6 xl:grid-cols-2">
        <!-- 缴纳保证金：enabled=false 时整块隐藏，仅提示 -->
        <section class="surface-card p-6 sm:p-8">
          <h2 class="text-2xl font-semibold text-ink-1">缴纳保证金</h2>
          <p class="mt-2 text-sm text-ink-2">从可用余额转入保证金，用于提升接单权益；余额不足时请先到钱包充值。</p>

          <div v-if="!overview.enabled" class="message-info mt-6">保证金功能未开启</div>

          <template v-else>
            <div v-if="inMessage.text" class="mt-4" :class="messageClass(inMessage.type)">{{ inMessage.text }}</div>

            <form class="mt-6 max-w-md space-y-5" @submit.prevent="submitIn">
              <div>
                <label class="label" for="deposit-in-amount">转入金额（元）</label>
                <input
                  id="deposit-in-amount"
                  v-model="inForm.amount"
                  type="number"
                  min="0.01"
                  step="0.01"
                  class="input min-h-[44px]"
                  :placeholder="`可用余额 ${formatPrice(overview.available_balance)}`"
                />
              </div>
              <button class="btn-primary w-full py-3 sm:w-auto sm:!px-10" :disabled="submittingIn || walletStore.depositSubmitting">
                {{ submittingIn ? '提交中...' : '从余额转入' }}
              </button>
            </form>
          </template>
        </section>

        <!-- 转回余额：can_return=false 时禁用并展示原因；保证金为 0 时也无可转回 -->
        <section class="surface-card p-6 sm:p-8">
          <h2 class="text-2xl font-semibold text-ink-1">转回余额</h2>
          <p class="mt-2 text-sm text-ink-2">把保证金转回可用余额。若存在未完成订单，需满足冷却时间后才可转回。</p>

          <div v-if="outMessage.text" class="mt-4" :class="messageClass(outMessage.type)">{{ outMessage.text }}</div>
          <div v-if="!canReturn && overview.return_block_reason" class="message-warning mt-4">
            {{ overview.return_block_reason }}
          </div>

          <form class="mt-6 max-w-md space-y-5" @submit.prevent="submitOut">
            <div>
              <label class="label" for="deposit-out-amount">转回金额（元）</label>
              <input
                id="deposit-out-amount"
                v-model="outForm.amount"
                type="number"
                min="0.01"
                step="0.01"
                class="input min-h-[44px]"
                :disabled="!canReturn"
                :placeholder="`保证金余额 ${formatPrice(overview.deposit_balance)}`"
              />
            </div>
            <button
              class="btn-primary w-full py-3 sm:w-auto sm:!px-10"
              :disabled="!canReturn || submittingOut || walletStore.depositSubmitting || Number(overview.deposit_balance) <= 0"
            >
              {{ submittingOut ? '提交中...' : '转回余额' }}
            </button>
          </form>
        </section>
      </div>

      <!-- 权益阶梯表：当前档位高亮 -->
      <section class="surface-card p-6 sm:p-8">
        <h2 class="text-2xl font-semibold text-ink-1">权益阶梯</h2>
        <p class="mt-2 text-sm text-ink-2">保证金门槛越高，接单等待越短、结账越快。</p>

        <div v-if="!overview.tiers.length" class="empty-state mt-6">
          <div class="empty-state__icon" aria-hidden="true">🪜</div>
          <h3 class="empty-state__title">暂无权益阶梯</h3>
          <p class="empty-state__copy">管理员配置后，各档位权益会展示在这里。</p>
        </div>

        <div v-else class="mt-6 overflow-x-auto">
          <table class="w-full min-w-[640px] border-collapse text-left text-sm">
            <thead>
              <tr class="text-xs uppercase tracking-[0.08em] text-ink-3">
                <th class="px-3 py-3 font-medium">保证金门槛</th>
                <th class="px-3 py-3 font-medium">优先接单</th>
                <th class="px-3 py-3 font-medium">接单免冻结赔付金</th>
                <th class="px-3 py-3 font-medium">结账时效</th>
                <th class="px-3 py-3 font-medium">档位</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="tier in overview.tiers"
                :key="tier.id"
                class="border-t border-line-1"
                :class="isCurrentTier(tier) ? 'bg-primary-soft' : ''"
              >
                <td class="px-3 py-3 font-semibold tabular-nums text-ink-1">{{ formatPrice(tier.threshold) }}</td>
                <td class="px-3 py-3 text-ink-2">{{ formatWaitSeconds(tier.wait_seconds) }}</td>
                <td class="px-3 py-3 text-ink-2">{{ tier.exempt_compensation ? '✅ 免冻结' : '❌ 需冻结' }}</td>
                <td class="px-3 py-3 text-ink-2">{{ formatSettleHours(tier.settle_hours) }}</td>
                <td class="px-3 py-3">
                  <span v-if="isCurrentTier(tier)" class="tag !bg-primary-soft !text-primary">当前档位</span>
                  <span v-else-if="tier.enabled === false" class="tag !bg-surface-3 !text-ink-2">已停用</span>
                  <span v-else class="text-ink-3">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🔒</div>
      <h3 class="empty-state__title">暂时无法查看保证金</h3>
      <p class="empty-state__copy">{{ loadError || '保证金信息暂不可用，请稍后重试。' }}</p>
    </section>
  </div>
</template>
