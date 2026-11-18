<script setup>
import { computed, onMounted } from 'vue'

import { formatDateTime, formatMoneyFixed } from '@/utils/display'
import { useAdminOrderAnalytics, useMyOrderAnalytics } from '@/components/admin/useAdminOrderAnalytics'

const props = defineProps({
  scope: { type: String, default: 'admin', validator: (value) => ['admin', 'self'].includes(value) },
})

const isSelf = computed(() => props.scope === 'self')
const analytics = isSelf.value ? useMyOrderAnalytics() : useAdminOrderAnalytics()

const periods = [
  { key: 'today', label: '今天' },
  { key: '7d', label: '近 7 天' },
  { key: '30d', label: '近 1 个月' },
]

const metrics = [
  { key: 'issued_orders', label: '发单数', metric: 'issued' },
  { key: 'cancelled_orders', label: '取消单', metric: 'cancelled' },
  { key: 'completed_orders', label: '完结单', metric: 'completed' },
  { key: 'settled_slots', label: '已结算名额', metric: 'settled' },
  { key: 'issued_amount', label: '未取消发单金额', metric: 'issued_amount', money: true },
  { key: 'successful_amount', label: '成功成交金额', metric: 'settled', money: true },
]

const summaryCards = computed(() => metrics.map((metric) => ({
  ...metric,
  value: analytics.summary.value?.[metric.key] ?? 0,
})))

const lifetimeCards = computed(() => metrics.filter((metric) => metric.key !== 'settled_slots').map((metric) => ({
  ...metric,
  value: analytics.lifetime.value?.[metric.key] ?? 0,
})))

const periodRange = computed(() => {
  const start = analytics.summary.value?.period_start
  const end = analytics.summary.value?.period_end
  return start && end ? `统计区间：${start} 至 ${end}（北京时间）` : '按北京时间自然日统计'
})

const selectedMetricLabel = computed(() => (
  ({
    issued: '发单记录',
    issued_amount: '未取消发单金额记录',
    cancelled: '取消订单记录',
    completed: '完结订单记录',
    settled: '已结算订单记录',
  })[analytics.selectedMetric.value] || '订单明细'
))

const metricEventLabel = computed(() => ({
  issued: '发单时间',
  issued_amount: '发单时间',
  cancelled: '取消时间',
  completed: '完结时间',
  settled: '结算时间',
})[analytics.selectedMetric.value] || '统计时间')

const metricAmountLabel = computed(() => ({
  issued: '订单金额',
  issued_amount: '未取消发单金额',
  cancelled: '取消订单金额',
  completed: '完结订单金额',
  settled: '成功成交金额',
})[analytics.selectedMetric.value] || '指标金额')

function dailyValue(row, key) {
  if (key === 'successful_amount') return row?.successful_amount ?? row?.settled_amount ?? 0
  if (key === 'issued_amount') return row?.issued_amount ?? 0
  if (key === 'issued_orders') return row?.issued_orders ?? row?.issued ?? 0
  if (key === 'cancelled_orders') return row?.cancelled_orders ?? row?.cancelled ?? 0
  if (key === 'completed_orders') return row?.completed_orders ?? row?.completed ?? 0
  return row?.[key] ?? 0
}

function dateLabel(value) {
  return String(value || '').slice(0, 10) || '—'
}

function detailId(item) {
  return item.order_id ?? item.id ?? '—'
}

function detailStatus(item) {
  return item.order_status ?? item.status_label ?? item.status ?? '—'
}

function detailAmount(item) {
  return formatMoneyFixed(item.metric_amount ?? item.amount ?? item.successful_amount ?? item.settled_amount ?? 0)
}

onMounted(analytics.loadSummary)
</script>

<template>
  <section class="space-y-4" aria-label="发单数据">
    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-xl font-semibold text-ink-1">{{ isSelf ? '我的发单数据' : '平台发单数据' }}</h2>
        <p class="mt-1 text-sm text-ink-2">
          {{ isSelf ? '仅统计当前账号的订单。' : '统计平台全部订单。' }}
          未取消发单金额与成功成交金额分开展示，成交额按已结算名额毛额计算，不扣服务费。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2" role="group" aria-label="统计周期">
        <button
          v-for="item in periods"
          :key="item.key"
          type="button"
          :class="[analytics.period.value === item.key ? 'filter-pill-active' : 'filter-pill', 'btn-compact']"
          :aria-pressed="analytics.period.value === item.key"
          @click="analytics.setPeriod(item.key)"
        >
          {{ item.label }}
        </button>
        <button type="button" class="btn-compact btn-secondary" :disabled="analytics.loading.value" @click="analytics.loadSummary">
          {{ analytics.loading.value ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </header>

    <p class="text-xs text-ink-3">{{ periodRange }}</p>

    <div v-if="analytics.error.value" class="message-error" role="alert">
      {{ analytics.error.value }}
      <button type="button" class="btn-compact btn-ghost ml-2" @click="analytics.loadSummary">重试</button>
    </div>

    <div v-if="analytics.loading.value && !analytics.summary.value" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true">
      <div v-for="item in periods.concat([{ key: 'placeholder' }])" :key="item.key" class="skeleton h-24 !rounded-tile"></div>
    </div>

    <template v-else-if="analytics.summary.value">
      <section v-if="isSelf && analytics.lifetime.value" class="space-y-3" aria-label="累计发单数据">
        <div class="flex items-center justify-between gap-2">
          <h3 class="text-base font-semibold text-ink-1">累计数据</h3>
          <span class="text-xs text-ink-3">当前账号历史订单总览</span>
        </div>
        <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
          <article v-for="card in lifetimeCards" :key="`lifetime-${card.key}`" class="stat-card !p-3">
            <p class="text-xs text-ink-2">累计{{ card.label }}</p>
            <p class="mt-1.5 text-xl font-semibold leading-6 tabular-nums" :class="card.money ? 'text-price' : 'text-ink-1'">
              {{ card.money ? formatMoneyFixed(card.value) : Number(card.value || 0).toLocaleString('zh-CN') }}
            </p>
          </article>
        </div>
      </section>

      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <article v-for="card in summaryCards" :key="card.key" class="stat-card">
          <p class="text-[13px] text-ink-2">{{ card.label }}</p>
          <p class="stat-value mt-1.5 tabular-nums" :class="card.money ? 'text-price' : 'text-ink-1'">
            {{ card.money ? formatMoneyFixed(card.value) : Number(card.value || 0).toLocaleString('zh-CN') }}
          </p>
          <p v-if="card.key === 'issued_amount'" class="mt-1 text-xs text-ink-3">非取消订单单价 × 名额</p>
          <p v-else-if="card.key === 'successful_amount'" class="mt-1 text-xs text-ink-3">已结算名额毛额</p>
          <p v-else class="mt-1 text-xs text-ink-3">点击日明细查看订单</p>
        </article>
      </div>

      <section class="surface-card p-4 sm:p-5" aria-label="每日发单明细">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 class="text-base font-semibold text-ink-1">每日明细</h3>
          <span class="text-xs text-ink-3">点击指标查看对应日期的订单</span>
        </div>

        <div v-if="analytics.loading.value" class="py-6 text-center text-sm text-ink-2" aria-live="polite">数据更新中…</div>
        <div v-else-if="!analytics.daily.value.length" class="empty-state py-8">
          <p class="empty-state__copy">所选周期暂无发单数据</p>
        </div>
        <div v-else class="overflow-x-auto">
          <table class="data-table min-w-[880px]">
            <thead>
              <tr>
                <th>日期</th>
                <th v-for="metric in metrics" :key="metric.key" :class="metric.money ? 'text-right' : ''">
                  {{ metric.label }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in analytics.daily.value" :key="row.date">
                <td class="whitespace-nowrap font-medium text-ink-1">{{ dateLabel(row.date) }}</td>
                <td v-for="metric in metrics" :key="metric.key" :class="metric.money ? 'text-right font-semibold tabular-nums' : ''">
                  <button
                    type="button"
                    :class="['btn-compact btn-ghost hover:underline', metric.money ? 'text-price' : 'text-primary']"
                    @click="analytics.openDetails(row.date, metric.metric)"
                  >
                    {{ metric.money
                      ? formatMoneyFixed(dailyValue(row, metric.key))
                      : Number(dailyValue(row, metric.key)).toLocaleString('zh-CN') }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="analytics.selectedDate.value" class="surface-card p-4 sm:p-5" aria-label="订单级明细" aria-live="polite">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="text-base font-semibold text-ink-1">{{ dateLabel(analytics.selectedDate.value) }} · {{ selectedMetricLabel }}</h3>
            <p class="mt-1 text-xs text-ink-3">共 {{ analytics.detailsTotal.value.toLocaleString('zh-CN') }} 条，金额列：{{ metricAmountLabel }}</p>
          </div>
          <button type="button" class="btn-compact btn-ghost" @click="analytics.closeDetails">收起明细</button>
        </div>

        <div v-if="analytics.detailsError.value" class="message-error flex flex-wrap items-center gap-2" role="alert">
          <span>{{ analytics.detailsError.value }}</span>
          <button
            type="button"
            class="btn-compact btn-ghost"
            :disabled="analytics.detailsLoading.value"
            @click="analytics.openDetails(analytics.selectedDate.value, analytics.selectedMetric.value)"
          >
            重试
          </button>
        </div>
        <div v-else-if="analytics.detailsLoading.value && !analytics.details.value.length" class="py-6 text-center text-sm text-ink-2" aria-busy="true">订单明细加载中…</div>
        <div v-else-if="!analytics.details.value.length" class="empty-state py-8">
          <p class="empty-state__copy">该日期没有符合条件的订单</p>
        </div>
        <div v-else class="overflow-x-auto">
          <table class="data-table min-w-[1160px]">
            <thead>
              <tr>
                <th>订单</th>
                <th>游戏</th>
                <th>发布人</th>
                <th>发单时间</th>
                <th>{{ metricEventLabel }}</th>
                <th>状态</th>
                <th>单价</th>
                <th>名额</th>
                <th>已结算</th>
                <th class="text-right">{{ metricAmountLabel }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in analytics.details.value" :key="`${detailId(item)}-${item.claim_id || ''}`">
                <td class="max-w-[260px]">
                  <span class="block text-xs text-ink-3">#{{ detailId(item) }}</span>
                  <span class="block truncate font-medium text-ink-1">{{ item.title || item.order_title || '未命名订单' }}</span>
                </td>
                <td>{{ item.game_name || item.game || '—' }}</td>
                <td>{{ item.publisher || item.publisher_name || '—' }}</td>
                <td class="whitespace-nowrap">{{ formatDateTime(item.created_at || item.issued_at) }}</td>
                <td class="whitespace-nowrap">{{ formatDateTime(item.event_at || item.created_at || item.issued_at) }}</td>
                <td><span class="tag">{{ detailStatus(item) }}</span></td>
                <td class="whitespace-nowrap tabular-nums">{{ formatMoneyFixed(item.unit_price ?? item.price ?? 0) }}</td>
                <td>{{ item.max_claims ?? item.slots ?? 0 }}</td>
                <td>{{ item.settled_slots ?? item.settled_claims ?? 0 }}</td>
                <td class="whitespace-nowrap text-right font-semibold tabular-nums text-price">{{ detailAmount(item) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="analytics.detailsPage.value < analytics.detailsPages.value" class="mt-3 flex justify-center">
          <button type="button" class="btn-compact btn-secondary" :disabled="analytics.detailsLoading.value" @click="analytics.loadMoreDetails">
            {{ analytics.detailsLoading.value ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </section>
    </template>
  </section>
</template>
