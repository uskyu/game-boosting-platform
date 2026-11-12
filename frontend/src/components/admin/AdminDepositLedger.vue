<script setup>
/**
 * 后台「保证金流水」。
 *
 * 老板要看的是：今天有没有人把保证金转回余额（退保证金），谁转的、多少钱。
 * 页面只消费 DEPOSIT_TRANSFER_IN / OUT 两类流水：
 * - 转入保证金：用户/管理员把可用余额划进保证金（deposit_change 为正）；
 * - 保证金转回：用户/管理员把保证金退回可用余额（deposit_change 为负）。
 * 默认北京时间今天，支持日期区间、方向、用户筛选；顶部汇总不随方向过滤变化。
 */
import { computed, onMounted, ref } from 'vue'

import { useAdminDepositLedgerStore } from '@/stores/adminDepositLedger'
import { formatCount, formatDateTime, formatPrice } from '@/utils/display'

const store = useAdminDepositLedgerStore()

const direction = ref('all')
const search = ref('')
const startDate = ref(productDateToday())
const endDate = ref(productDateToday())
const notice = ref('')

const items = computed(() => store.items)
const stats = computed(() => store.stats)
const pagination = computed(() => store.pagination)
const loading = computed(() => store.loading)
const error = computed(() => store.error)
// 管理员手动扣减也记 DEPOSIT_TRANSFER_OUT；单独拆出来，避免“退保证金人数”被后台操作灌水
const adminOutAmount = computed(() =>
  Math.max(0, Number(stats.value.out_amount || 0) - Number(stats.value.self_out_amount || 0))
)

const DIRECTION_OPTIONS = [
  { value: 'all', label: '全部' },
  { value: 'in', label: '转入保证金' },
  { value: 'out', label: '保证金转回' },
]

function productDateToday() {
  // 产品时区固定 Asia/Shanghai；en-CA 输出 YYYY-MM-DD，正好是 input[type=date] 的格式
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
}

function directionLabel(item) {
  return item.direction === 'in' ? '转入保证金' : '保证金转回'
}

function operatorLabel(item) {
  if (item.operator_id) {
    return `管理员 ${item.operator_username || `#${item.operator_id}`}`
  }
  return '用户自助'
}

function signedMoney(value) {
  const amount = Number(value ?? 0)
  if (amount > 0) return `+${formatPrice(amount)}`
  if (amount < 0) return `-${formatPrice(Math.abs(amount))}`
  return formatPrice(0)
}

function availableChangeText(item) {
  // 管理员调保证金只动保证金池，可用余额不变；显示 ¥0 容易被误读成“扣了 0 元”
  return item.operator_id ? '不动可用余额' : signedMoney(item.available_change)
}

function showAvailableFlow(item) {
  return !item.operator_id
}

function moneyClass(value) {
  const amount = Number(value ?? 0)
  if (amount > 0) return 'text-success'
  if (amount < 0) return 'text-warning'
  return 'text-ink-2'
}

function depositChangeClass(item) {
  return item.deposit_change > 0 ? 'text-success' : 'text-warning'
}

async function load(page = 1) {
  notice.value = ''
  if (startDate.value && endDate.value && startDate.value > endDate.value) {
    notice.value = '结束日期不能早于开始日期'
    return
  }
  const result = await store.fetchLedger({
    page,
    startDate: startDate.value,
    endDate: endDate.value,
    direction: direction.value,
    q: search.value.trim(),
  })
  if (!result.success) notice.value = result.error
}

function setDirection(value) {
  direction.value = value
  load(1)
}

function handleSearch() {
  load(1)
}

function handleReset() {
  search.value = ''
  direction.value = 'all'
  startDate.value = productDateToday()
  endDate.value = productDateToday()
  load(1)
}

function handlePage(page) {
  if (page < 1 || page > (pagination.value.pages || 1)) return
  load(page)
}

onMounted(() => load(1))
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-2xl font-semibold text-ink-1">保证金流水</h2>
        <p class="mt-2 text-sm text-ink-2">
          余额转入保证金、保证金转回余额（含管理员手动调整），默认看北京时间今天。
          转回余额即用户把保证金退回可用余额；之后是否提现，请到「提现处理」查看。
        </p>
      </div>
    </div>

    <div class="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <div class="rounded-2xl border border-line bg-surface-2 p-4">
        <p class="text-xs text-ink-3">转入保证金（含管理员调整）</p>
        <p class="mt-1 text-2xl font-semibold tabular-nums text-success">{{ formatPrice(stats.in_amount) }}</p>
        <p class="mt-1 text-xs text-ink-3">{{ formatCount(stats.in_count) }} 笔</p>
      </div>
      <div class="rounded-2xl border border-line bg-surface-2 p-4">
        <p class="text-xs text-ink-3">保证金转回（含管理员调整）</p>
        <p class="mt-1 text-2xl font-semibold tabular-nums text-warning">{{ formatPrice(stats.out_amount) }}</p>
        <p class="mt-1 text-xs text-ink-3">
          {{ formatCount(stats.out_count) }} 笔 · 用户自助 {{ formatPrice(stats.self_out_amount) }} / {{ formatCount(stats.self_out_users) }} 人
          <span v-if="adminOutAmount > 0"> · 管理员调整 {{ formatPrice(adminOutAmount) }}</span>
        </p>
      </div>
      <div class="rounded-2xl border border-line bg-surface-2 p-4">
        <p class="text-xs text-ink-3">用户自助转回人数</p>
        <p class="mt-1 text-2xl font-semibold tabular-nums text-ink-1">{{ formatCount(stats.self_out_users) }}</p>
        <p class="mt-1 text-xs text-ink-3">不含管理员调整</p>
      </div>
      <div class="rounded-2xl border border-line bg-surface-2 p-4">
        <p class="text-xs text-ink-3">净变化</p>
        <p class="mt-1 text-2xl font-semibold tabular-nums" :class="moneyClass(stats.net_amount)">{{ signedMoney(stats.net_amount) }}</p>
        <p class="mt-1 text-xs text-ink-3">转入 - 转回</p>
      </div>
    </div>

    <div v-if="notice" class="mt-4 message-error">{{ notice }}</div>
    <div v-if="error && !notice" class="mt-4 message-error">{{ error }}</div>

    <div class="mt-5 flex flex-wrap items-end gap-3">
      <div>
        <label class="label" for="deposit-ledger-start">开始日期</label>
        <input id="deposit-ledger-start" v-model="startDate" type="date" class="input min-h-[44px]" />
      </div>
      <div>
        <label class="label" for="deposit-ledger-end">结束日期</label>
        <input id="deposit-ledger-end" v-model="endDate" type="date" class="input min-h-[44px]" />
      </div>
      <div class="min-w-[180px] flex-1">
        <label class="label" for="deposit-ledger-search">用户</label>
        <input
          id="deposit-ledger-search"
          v-model="search"
          type="search"
          class="input min-h-[44px]"
          placeholder="用户名 / 邮箱 / 用户ID"
          @keyup.enter="handleSearch"
        />
      </div>
      <div class="flex flex-wrap gap-2 pb-0.5" role="group" aria-label="保证金流向">
        <button
          v-for="option in DIRECTION_OPTIONS"
          :key="option.value"
          type="button"
          :class="direction === option.value ? 'tab-pill-active' : 'tab-pill'"
          @click="setDirection(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
      <button type="button" class="btn-primary min-h-[44px] !px-5 !py-2" @click="handleSearch">查询</button>
      <button type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="handleReset">重置</button>
    </div>

    <div class="mt-5 overflow-x-auto">
      <table class="w-full min-w-[880px] text-left text-sm">
        <thead>
          <tr class="border-b border-line text-xs text-ink-3">
            <th class="whitespace-nowrap px-2 py-3 font-medium">时间</th>
            <th class="px-2 py-3 font-medium">用户</th>
            <th class="whitespace-nowrap px-2 py-3 font-medium">方向</th>
            <th class="whitespace-nowrap px-2 py-3 text-right font-medium">保证金变动</th>
            <th class="whitespace-nowrap px-2 py-3 text-right font-medium">可用余额变动</th>
            <th class="px-2 py-3 font-medium">备注</th>
            <th class="whitespace-nowrap px-2 py-3 font-medium">操作人</th>
          </tr>
        </thead>
        <tbody v-if="items.length">
          <tr v-for="item in items" :key="item.id" class="border-b border-line/70 align-top">
            <td class="whitespace-nowrap px-2 py-3 tabular-nums text-ink-2">{{ formatDateTime(item.created_at) }}</td>
            <td class="px-2 py-3">
              <p class="font-medium text-ink-1">{{ item.username || `用户 #${item.user_id}` }}</p>
              <p class="mt-0.5 text-xs text-ink-3">#{{ item.user_id }} · {{ item.email }}</p>
            </td>
            <td class="whitespace-nowrap px-2 py-3">
              <span class="tag" :class="item.direction === 'in' ? '!bg-success-soft !text-success' : '!bg-warning-soft !text-warning'">
                {{ directionLabel(item) }}
              </span>
            </td>
            <td class="whitespace-nowrap px-2 py-3 text-right font-semibold tabular-nums" :class="depositChangeClass(item)">
              {{ signedMoney(item.deposit_change) }}
            </td>
            <td class="whitespace-nowrap px-2 py-3 text-right tabular-nums">
              <p :class="moneyClass(item.available_change)">{{ availableChangeText(item) }}</p>
              <p v-if="showAvailableFlow(item)" class="mt-0.5 text-xs text-ink-3">
                {{ formatPrice(item.available_before) }} → {{ formatPrice(item.available_after) }}
              </p>
            </td>
            <td class="px-2 py-3 text-ink-2">
              <p>{{ item.remark || '—' }}</p>
              <p v-if="item.order_id" class="mt-0.5 text-xs text-ink-3">订单 #{{ item.order_id }}</p>
            </td>
            <td class="whitespace-nowrap px-2 py-3 text-ink-2">{{ operatorLabel(item) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="loading" class="py-8 text-center text-sm text-ink-3">加载中…</p>
      <p v-else-if="!items.length" class="py-10 text-center text-sm text-ink-3">
        这段时间没有保证金转入/转回记录。
      </p>
    </div>

    <div class="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-2">
      <p>共 {{ formatCount(pagination.total) }} 条 · {{ pagination.page }} / {{ pagination.pages || 1 }} 页</p>
      <div class="flex gap-2">
        <button type="button" class="btn-secondary min-h-[40px] !px-4 !py-2" :disabled="pagination.page <= 1" @click="handlePage(pagination.page - 1)">上一页</button>
        <button type="button" class="btn-secondary min-h-[40px] !px-4 !py-2" :disabled="pagination.page >= (pagination.pages || 1)" @click="handlePage(pagination.page + 1)">下一页</button>
      </div>
    </div>
  </section>
</template>
