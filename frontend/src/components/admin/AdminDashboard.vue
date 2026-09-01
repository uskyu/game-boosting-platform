<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import EChart from '@/components/charts/EChart.vue'
import api from '@/utils/api'

const router = useRouter()

const overview = ref(null)
const orderTrend = ref(null)
const gameDist = ref(null)
const boosterRank = ref(null)
const userGrowth = ref(null)
const loading = ref(true)
const trendPeriod = ref('day')
const trendDays = ref(30)

// 主题感知：读 CSS 变量的实际值（亮/暗各自取色），class 翻转时 bump 让图表选项重算
const themeTick = ref(0)
let themeObserver = null

function cssVar(name, fallback = '#6e6e73') {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return fallback
  }
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

const chartColors = computed(() => {
  void themeTick.value
  return {
    ink1: cssVar('--text-1', '#1d1d1f'),
    ink2: cssVar('--text-2', '#6e6e73'),
    line1: cssVar('--line-1', 'rgba(0,0,0,0.06)'),
    line2: cssVar('--line-2', 'rgba(0,0,0,0.12)'),
    primary: cssVar('--primary', '#0071e3'),
    price: cssVar('--price', '#e8003a'),
    success: cssVar('--success', '#248a3d'),
    warning: cssVar('--warning', '#b25000'),
    danger: cssVar('--danger', '#d70015'),
    info: cssVar('--info', '#0071e3'),
    surface: cssVar('--surface', '#ffffff'),
  }
})

const overviewCards = computed(() => {
  if (!overview.value) return []
  const o = overview.value
  return [
    { label: '总用户', value: o.total_users, color: 'text-ink-1' },
    { label: '代练', value: o.total_boosters, color: 'text-info' },
    { label: '总订单', value: o.total_orders, color: 'text-ink-1' },
    { label: '总收入', value: `¥${o.total_revenue.toFixed(2)}`, color: 'text-price' },
    { label: '待接单', value: o.pending_orders, color: 'text-warning' },
    { label: '进行中', value: o.active_orders, color: 'text-info' },
    { label: '已完成', value: o.completed_orders, color: 'text-success' },
    { label: '争议', value: o.disputed_orders, color: 'text-danger' },
  ]
})

const orderTrendOption = computed(() => {
  if (!orderTrend.value?.points?.length) return null
  const pts = orderTrend.value.points
  const c = chartColors.value
  void themeTick.value
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['订单数', '金额'], textStyle: { color: c.ink2 } },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category',
      data: pts.map((p) => p.date),
      axisLabel: { color: c.ink2, fontSize: 11 },
      axisLine: { lineStyle: { color: c.line2 } },
    },
    yAxis: [
      { type: 'value', name: '订单数', axisLabel: { color: c.ink2 }, splitLine: { lineStyle: { color: c.line1 } } },
      { type: 'value', name: '金额(¥)', axisLabel: { color: c.ink2 }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '订单数',
        type: 'bar',
        data: pts.map((p) => p.count),
        itemStyle: { color: c.primary, borderRadius: [4, 4, 0, 0] },
      },
      {
        name: '金额',
        type: 'line',
        yAxisIndex: 1,
        data: pts.map((p) => p.revenue),
        smooth: true,
        lineStyle: { color: c.price, width: 2 },
        itemStyle: { color: c.price },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: hexToRgba(c.price, 0.18) },
              { offset: 1, color: hexToRgba(c.price, 0) },
            ],
          },
        },
      },
    ],
  }
})

const gameDistOption = computed(() => {
  if (!gameDist.value?.items?.length) return null
  const items = gameDist.value.items
  const c = chartColors.value
  void themeTick.value
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', formatter: '{b}: {c}单 ({d}%)' },
    legend: { type: 'scroll', bottom: 0, textStyle: { color: c.ink2, fontSize: 11 } },
    series: [
      {
        type: 'pie',
        radius: ['35%', '65%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: c.surface, borderWidth: 2 },
        label: { color: c.ink1, fontSize: 11 },
        emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
        data: items.map((g) => ({ value: g.count, name: g.game_name })),
      },
    ],
  }
})

const userGrowthOption = computed(() => {
  if (!userGrowth.value?.points?.length) return null
  const pts = userGrowth.value.points
  const c = chartColors.value
  void themeTick.value
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['新增用户', '累计用户'], textStyle: { color: c.ink2 } },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category',
      data: pts.map((p) => p.date),
      axisLabel: { color: c.ink2, fontSize: 11 },
      axisLine: { lineStyle: { color: c.line2 } },
    },
    yAxis: [
      { type: 'value', name: '新增', axisLabel: { color: c.ink2 }, splitLine: { lineStyle: { color: c.line1 } } },
      { type: 'value', name: '累计', axisLabel: { color: c.ink2 }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '新增用户',
        type: 'bar',
        data: pts.map((p) => p.new_users),
        itemStyle: { color: c.info, borderRadius: [4, 4, 0, 0] },
      },
      {
        name: '累计用户',
        type: 'line',
        yAxisIndex: 1,
        data: pts.map((p) => p.cumulative),
        smooth: true,
        lineStyle: { color: c.success, width: 2 },
        itemStyle: { color: c.success },
      },
    ],
  }
})

async function fetchAll() {
  loading.value = true
  try {
    const [ovRes, trendRes, distRes, rankRes, growthRes] = await Promise.all([
      api.get('/admin/dashboard/overview'),
      api.get('/admin/dashboard/order-trend', { params: { period: trendPeriod.value, days: trendDays.value } }),
      api.get('/admin/dashboard/game-distribution'),
      api.get('/admin/dashboard/booster-ranking', { params: { limit: 10 } }),
      api.get('/admin/dashboard/user-growth', { params: { days: trendDays.value } }),
    ])
    overview.value = ovRes.data
    orderTrend.value = trendRes.data
    gameDist.value = distRes.data
    boosterRank.value = rankRes.data
    userGrowth.value = growthRes.data
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

async function changeTrendPeriod(period) {
  trendPeriod.value = period
  try {
    const res = await api.get('/admin/dashboard/order-trend', { params: { period, days: trendDays.value } })
    orderTrend.value = res.data
  } catch {
    // ignore
  }
}

function levelColor(level) {
  const c = chartColors.value
  void themeTick.value
  const map = {
    master: c.price,
    diamond: c.info,
    gold: c.warning,
    silver: c.ink3,
    bronze: c.ink2,
  }
  return map[level] || c.ink2
}

function hexToRgba(hex, alpha) {
  const safe = String(hex || '').replace('#', '').trim()
  if (safe.length !== 6) {
    return `rgba(0, 0, 0, ${alpha})`
  }
  const red = Number.parseInt(safe.slice(0, 2), 16)
  const green = Number.parseInt(safe.slice(2, 4), 16)
  const blue = Number.parseInt(safe.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

onMounted(fetchAll)

onMounted(() => {
  if (typeof MutationObserver === 'undefined') {
    return
  }
  themeObserver = new MutationObserver(() => {
    themeTick.value += 1
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

onBeforeUnmount(() => {
  themeObserver?.disconnect()
  themeObserver = null
})
</script>

<template>
  <div class="space-y-6">
    <!-- Loading -->
    <div v-if="loading" class="space-y-6" aria-busy="true">
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div v-for="n in 8" :key="`ov-skeleton-${n}`" class="skeleton h-24 !rounded-tile"></div>
      </div>
      <div class="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <div class="skeleton h-[380px] !rounded-card"></div>
        <div class="skeleton h-[380px] !rounded-card"></div>
      </div>
    </div>

    <template v-else>
      <!-- Overview cards -->
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <article v-for="card in overviewCards" :key="card.label" class="stat-card">
          <p class="text-[13px] text-ink-2">{{ card.label }}</p>
          <p class="stat-value mt-1.5" :class="card.color">{{ card.value }}</p>
        </article>
      </div>

      <!-- Charts row: Order Trend + Game Distribution -->
      <div class="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <section class="surface-card p-5">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h3 class="text-lg font-semibold text-ink-1">订单趋势</h3>
            <div class="flex gap-2">
              <button
                v-for="p in ['day', 'week', 'month']"
                :key="p"
                class="filter-pill !py-1.5 text-xs"
                :class="trendPeriod === p ? 'filter-pill-active' : ''"
                @click="changeTrendPeriod(p)"
              >
                {{ p === 'day' ? '日' : p === 'week' ? '周' : '月' }}
              </button>
            </div>
          </div>
          <EChart v-if="orderTrendOption" :option="orderTrendOption" height="320px" class="mt-4" />
          <div v-else class="empty-state mt-4 !py-10">
            <div class="empty-state__icon !h-11 !w-11 !text-lg" aria-hidden="true">📈</div>
            <p class="empty-state__copy">暂无趋势数据</p>
          </div>
        </section>

        <section class="surface-card p-5">
          <h3 class="text-lg font-semibold text-ink-1">游戏分布</h3>
          <EChart v-if="gameDistOption" :option="gameDistOption" height="320px" class="mt-4" />
          <div v-else class="empty-state mt-4 !py-10">
            <div class="empty-state__icon !h-11 !w-11 !text-lg" aria-hidden="true">🎮</div>
            <p class="empty-state__copy">暂无分布数据</p>
          </div>
        </section>
      </div>

      <!-- Charts row: User Growth -->
      <section class="surface-card p-5">
        <h3 class="text-lg font-semibold text-ink-1">用户增长</h3>
        <EChart v-if="userGrowthOption" :option="userGrowthOption" height="280px" class="mt-4" />
        <div v-else class="empty-state mt-4 !py-10">
          <div class="empty-state__icon !h-11 !w-11 !text-lg" aria-hidden="true">👥</div>
          <p class="empty-state__copy">暂无增长数据</p>
        </div>
      </section>

      <!-- Booster ranking table -->
      <section class="surface-card p-5">
        <h3 class="text-lg font-semibold text-ink-1">代练排行榜</h3>

        <div v-if="!boosterRank?.items?.length" class="empty-state mt-4 !py-10">
          <div class="empty-state__icon !h-11 !w-11 !text-lg" aria-hidden="true">🏆</div>
          <p class="empty-state__copy">暂无排行数据</p>
        </div>

        <div v-else class="mt-4 overflow-x-auto">
          <table class="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>代练</th>
                <th>信誉</th>
                <th>等级</th>
                <th>完成</th>
                <th>评分</th>
                <th>收入</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(b, idx) in boosterRank.items"
                :key="b.user_id"
                class="cursor-pointer"
                @click="router.push(`/booster/${b.user_id}`)"
              >
                <td class="font-semibold" :class="idx < 3 ? 'text-price' : 'text-ink-2'">{{ idx + 1 }}</td>
                <td>
                  <div class="flex items-center gap-2">
                    <div class="flex h-8 w-8 items-center justify-center rounded-tile border border-line-1 bg-primary-soft text-sm font-semibold text-primary">
                      {{ b.username?.slice(0, 1) }}
                    </div>
                    <span class="text-ink-1">{{ b.username }}</span>
                  </div>
                </td>
                <td class="text-ink-1">{{ b.credit_score }}</td>
                <td>
                  <span class="tag !px-2.5 !py-0.5 !text-[11px]" :style="{ color: levelColor(b.credit_level), borderColor: levelColor(b.credit_level) + '55', backgroundColor: levelColor(b.credit_level) + '1c' }">
                    {{ b.credit_level }}
                  </span>
                </td>
                <td class="text-ink-1">{{ b.total_completed }}</td>
                <td class="text-warning">{{ b.avg_rating.toFixed(1) }}</td>
                <td class="font-semibold tabular-nums text-price">¥{{ b.total_revenue.toFixed(2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
