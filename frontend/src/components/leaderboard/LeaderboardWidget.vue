<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import api from '@/utils/api'
import { formatPrice } from '@/utils/display'

/**
 * 订单大厅「每日排行榜」悬浮入口。
 *
 * 榜单口径由后端统一：按周期内已接单金额排序，接手即计入、取消后扣除，
 * 前 30 名 + 当前用户自己的名次与距上一名差额。这里只负责展示与交互，
 * 不展示游戏类别，也不把打手按游戏拆分。
 */

const PERIODS = [
  { value: 'day', label: '日榜', caption: '按接单金额排序 · 每日 24:00 重置' },
  { value: 'week', label: '周榜', caption: '按接单金额排序 · 每周一 00:00 重置' },
  { value: 'all', label: '总榜', caption: '按累计接单金额排序' },
]

const STORAGE_KEY = 'gdx_leaderboard_float_pos_v1'
const DRAG_THRESHOLD = 6

const open = ref(false)
const period = ref('day')
const loading = ref(false)
const error = ref('')
const data = ref(null)
let requestSeq = 0

const floatY = ref(null)
const dragging = ref(false)
const dragState = {
  pointerId: null,
  startX: 0,
  startY: 0,
  originY: 0,
  moved: false,
}
let suppressClick = false

const currentCaption = computed(
  () => PERIODS.find((item) => item.value === period.value)?.caption || '',
)
const items = computed(() => data.value?.items || [])
const my = computed(() => data.value?.me || { rank: null, amount: 0, gap_to_previous: null })
const podiumEntries = computed(() => {
  const list = items.value
  return [
    { entry: list[1] || null, rank: 2 },
    { entry: list[0] || null, rank: 1 },
    { entry: list[2] || null, rank: 3 },
  ]
})
const listEntries = computed(() => items.value.slice(3))
const hasAnyEntry = computed(() => items.value.length > 0)
const listRangeText = computed(() => {
  const list = listEntries.value
  if (!list.length) return ''
  return `第 ${list[0].rank} - ${list[list.length - 1].rank} 名`
})

const myRankText = computed(() => {
  if (!my.value.rank) return '暂未上榜'
  return `#${my.value.rank}`
})

const myGapText = computed(() => {
  if (!my.value.rank) return '本期接单后自动计入'
  if (my.value.rank === 1) return '当前第 1 名，继续保持'
  if (my.value.gap_to_previous == null) return ''
  return `距上一名还差 ${formatPrice(my.value.gap_to_previous)}`
})

function crownClasses(rank) {
  if (rank === 1) return 'leaderboard-crown--gold'
  if (rank === 2) return 'leaderboard-crown--silver'
  return 'leaderboard-crown--bronze'
}

function avatarText(entry) {
  return (entry?.username || '?').slice(0, 1)
}

function clampY(y) {
  const margin = 8
  const height = 78
  const maxY = Math.max(margin, window.innerHeight - height - margin)
  return Math.min(Math.max(Number(y) || 0, margin), maxY)
}

function defaultY() {
  const mobile = window.innerWidth < 768
  return clampY(window.innerHeight - (mobile ? 170 : 150))
}

function restorePosition() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    // 兼容旧版保存的 {x,y}：横向位置不再使用，始终贴右侧；只恢复纵向位置。
    if (saved && Number.isFinite(saved.y)) {
      floatY.value = clampY(saved.y)
      return
    }
  } catch {
    // localStorage 不可用或内容损坏时直接用默认位置
  }
  floatY.value = defaultY()
}

function savePosition() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ y: floatY.value }))
  } catch {
    // 忽略隐私模式/配额异常
  }
}

function onPointerDown(event) {
  if (event.button !== undefined && event.button !== 0) return
  // 上一次拖动的补发 click 可能没来；新一次按下时先解除抑制
  suppressClick = false
  const target = event.currentTarget
  dragState.pointerId = event.pointerId
  dragState.startX = event.clientX
  dragState.startY = event.clientY
  dragState.originY = floatY.value ?? 0
  dragState.moved = false
  dragging.value = true
  target?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event) {
  if (!dragging.value || event.pointerId !== dragState.pointerId) return
  const dx = event.clientX - dragState.startX
  const dy = event.clientY - dragState.startY
  if (!dragState.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
  dragState.moved = true
  // 悬浮球始终贴住可见区域右侧，只允许上下拖动调整纵向位置。
  floatY.value = clampY(dragState.originY + dy)
}

function finishDrag(event) {
  if (!dragging.value) return
  dragging.value = false
  try {
    event.currentTarget?.releasePointerCapture?.(event.pointerId)
  } catch {
    // 指针已释放时忽略
  }
  dragState.pointerId = null
  if (dragState.moved) {
    savePosition()
    // 拖动结束浏览器仍会补发一次 click，这里吞掉，避免松手后弹窗
    suppressClick = true
  }
}

function onFloatClick() {
  if (suppressClick) {
    suppressClick = false
    return
  }
  openBoard()
}

function onResize() {
  floatY.value = clampY(floatY.value ?? 0)
}

async function fetchLeaderboard() {
  const seq = ++requestSeq
  loading.value = true
  error.value = ''
  try {
    const response = await api.get('/leaderboard', {
      params: { period: period.value },
      timeout: 10000,
    })
    if (seq !== requestSeq) return
    data.value = response.data
  } catch (err) {
    if (seq !== requestSeq) return
    error.value = err?.message || '排行榜加载失败，请稍后重试'
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function openBoard() {
  open.value = true
  fetchLeaderboard()
}

function closeBoard() {
  open.value = false
}

function switchPeriod(value) {
  if (period.value === value) return
  period.value = value
  fetchLeaderboard()
}

function onKeydown(event) {
  if (event.key === 'Escape' && open.value) closeBoard()
}

watch(open, (value) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = value ? 'hidden' : ''
})

onMounted(() => {
  restorePosition()
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKeydown)
  if (typeof document !== 'undefined') document.body.style.overflow = ''
})
</script>

<template>
  <!-- 悬浮球：始终贴住可见区域最右侧，可上下拖动 -->
  <teleport to="body">
    <button
      type="button"
      class="leaderboard-float"
      :class="{ 'leaderboard-float--dragging': dragging }"
      :style="{ top: `${floatY ?? 0}px` }"
      v-show="floatY !== null"
      aria-label="打开每日排行榜，可上下拖动调整位置"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="finishDrag"
      @pointercancel="finishDrag"
      @click.prevent="onFloatClick"
    >
      <span class="leaderboard-float__icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M8 21h8" />
          <path d="M12 17v4" />
          <path d="M7 4h10v5a5 5 0 0 1-10 0V4Z" />
          <path d="M7 6H4.5A1.5 1.5 0 0 0 3 7.5v1A2.5 2.5 0 0 0 5.5 11H7" />
          <path d="M17 6h2.5A1.5 1.5 0 0 1 21 7.5v1a2.5 2.5 0 0 1-2.5 2.5H17" />
          <path d="M12 14v3" />
        </svg>
      </span>
      <span class="leaderboard-float__label">每日排行榜</span>
    </button>
  </teleport>

  <teleport to="body">
    <div
      v-if="open"
      class="leaderboard-scrim"
      @click.self="closeBoard"
    >
      <section
        class="leaderboard-panel"
        role="dialog"
        aria-modal="true"
        aria-label="打手排行榜"
      >
        <header class="leaderboard-header">
          <div>
            <h2 class="leaderboard-title">每日排行榜</h2>
            <p class="leaderboard-subtitle">{{ currentCaption }}</p>
          </div>
          <button type="button" class="leaderboard-close" aria-label="关闭排行榜" @click="closeBoard">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </header>

        <div class="leaderboard-tabs" role="tablist" aria-label="榜单周期">
          <button
            v-for="item in PERIODS"
            :key="item.value"
            type="button"
            role="tab"
            :aria-selected="period === item.value"
            :class="['leaderboard-tab', { 'leaderboard-tab--active': period === item.value }]"
            @click="switchPeriod(item.value)"
          >
            {{ item.label }}
          </button>
        </div>

        <div class="leaderboard-body">
          <div v-if="loading && !data" class="leaderboard-loading" aria-busy="true">
            <div class="leaderboard-skeleton leaderboard-skeleton--podium"></div>
            <div v-for="n in 5" :key="`lb-skeleton-${n}`" class="leaderboard-skeleton"></div>
          </div>

          <div v-else-if="error && !data" class="leaderboard-empty">
            <p class="leaderboard-empty__icon" aria-hidden="true">📡</p>
            <p class="leaderboard-empty__title">排行榜加载失败</p>
            <p class="leaderboard-empty__copy">{{ error }}</p>
            <button type="button" class="leaderboard-retry" @click="fetchLeaderboard">重新加载</button>
          </div>

          <div v-else-if="!hasAnyEntry" class="leaderboard-empty">
            <p class="leaderboard-empty__icon" aria-hidden="true">🏆</p>
            <p class="leaderboard-empty__title">本期还没有人接单</p>
            <p class="leaderboard-empty__copy">快去大厅抢单，第一单就会出现在这里。</p>
          </div>

          <template v-else>
            <!-- 前三名领奖台：2 / 1 / 3 排列，金 / 银 / 铜皇冠 -->
            <div class="leaderboard-podium">
              <div
                v-for="slot in podiumEntries"
                :key="`podium-${slot.rank}`"
                class="leaderboard-podium__slot"
                :class="`leaderboard-podium__slot--${slot.rank}`"
              >
                <template v-if="slot.entry">
                  <span class="leaderboard-crown" :class="crownClasses(slot.rank)" aria-hidden="true">
                    <svg viewBox="0 0 24 16" fill="currentColor">
                      <path d="M2 13.5 1 4.2l5.1 3.1L12 .8l5.9 6.5L23 4.2l-1 9.3H2Z" />
                      <rect x="2" y="14.2" width="20" height="1.4" rx="0.7" />
                    </svg>
                  </span>
                  <div class="leaderboard-podium__avatar" :class="{ 'leaderboard-podium__avatar--first': slot.rank === 1 }">
                    <img v-if="slot.entry.avatar_url" :src="slot.entry.avatar_url" :alt="slot.entry.username" loading="lazy" />
                    <span v-else>{{ avatarText(slot.entry) }}</span>
                  </div>
                  <p class="leaderboard-podium__name">{{ slot.entry.username }}</p>
                  <p class="leaderboard-podium__amount">{{ formatPrice(slot.entry.amount) }}</p>
                </template>
                <template v-else>
                  <span class="leaderboard-crown leaderboard-crown--empty" aria-hidden="true">·</span>
                  <div class="leaderboard-podium__avatar leaderboard-podium__avatar--empty">—</div>
                  <p class="leaderboard-podium__name leaderboard-podium__name--empty">虚位以待</p>
                  <p class="leaderboard-podium__amount leaderboard-podium__amount--empty">¥0.00</p>
                </template>
              </div>
            </div>

            <!-- 第 4 名起：桌面端独立成右栏并单独滚动 -->
            <section v-if="listEntries.length" class="leaderboard-ranklist">
              <p class="leaderboard-ranklist__title">{{ listRangeText }}</p>
              <div class="leaderboard-list">
                <div
                  v-for="entry in listEntries"
                  :key="entry.user_id"
                  class="leaderboard-row"
                >
                  <span class="leaderboard-row__rank">{{ entry.rank }}</span>
                  <span class="leaderboard-row__avatar">
                    <img v-if="entry.avatar_url" :src="entry.avatar_url" :alt="entry.username" loading="lazy" />
                    <span v-else>{{ avatarText(entry) }}</span>
                  </span>
                  <span class="leaderboard-row__name">{{ entry.username }}</span>
                  <span class="leaderboard-row__amount">{{ formatPrice(entry.amount) }}</span>
                </div>
              </div>
            </section>
          </template>
        </div>

        <!-- 我的排名：只展示自己与上一名的金额差 -->
        <footer class="leaderboard-me">
          <div class="leaderboard-me__left">
            <span class="leaderboard-me__label">我的排名</span>
            <strong class="leaderboard-me__rank">{{ myRankText }}</strong>
          </div>
          <div class="leaderboard-me__right">
            <p class="leaderboard-me__gap">{{ myGapText }}</p>
            <p v-if="my.rank" class="leaderboard-me__amount">本期 {{ formatPrice(my.amount) }}</p>
          </div>
        </footer>
      </section>
    </div>
  </teleport>
</template>

<style scoped>
.leaderboard-float {
  position: fixed;
  z-index: 45;
  /* 始终贴住可见区域最右侧：横向不允许拖动，只保留纵向调整 */
  right: 4px;
  display: flex;
  width: 64px;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  border: 0;
  background: transparent;
  padding: 0;
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  filter: drop-shadow(0 8px 18px rgba(15, 23, 42, 0.12));
  transition: transform 160ms ease;
}
.leaderboard-float:hover {
  transform: translateY(-2px);
}
.leaderboard-float--dragging {
  cursor: grabbing;
  transform: scale(1.04);
}
.leaderboard-float__icon {
  display: flex;
  height: 52px;
  width: 52px;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--primary);
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.10);
}
.leaderboard-float__icon svg {
  height: 27px;
  width: 27px;
}
.leaderboard-float__label {
  border-radius: 9999px;
  border: 1px solid var(--line-1);
  background: var(--surface);
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--ink-2);
  white-space: nowrap;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
}

.leaderboard-scrim {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.32);
  padding: 16px;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
.leaderboard-panel {
  display: flex;
  max-height: min(88dvh, 720px);
  width: min(100%, 420px);
  flex-direction: column;
  overflow: hidden;
  border-radius: 22px;
  border: 1px solid var(--line-1);
  background: var(--surface);
  color: var(--ink-1);
  box-shadow: var(--shadow-pop);
}
.leaderboard-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 20px 8px;
}
.leaderboard-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: var(--ink-1);
}
.leaderboard-subtitle {
  margin: 5px 0 0;
  font-size: 12px;
  color: var(--ink-3);
}
.leaderboard-close {
  display: flex;
  height: 32px;
  width: 32px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  border: 1px solid var(--line-1);
  background: var(--surface-3);
  color: var(--ink-2);
  cursor: pointer;
  transition: background 150ms ease, color 150ms ease;
}
.leaderboard-close:hover {
  background: var(--surface-2);
  color: var(--ink-1);
}
.leaderboard-close svg {
  height: 16px;
  width: 16px;
}
.leaderboard-tabs {
  display: flex;
  gap: 6px;
  padding: 4px 20px 12px;
}
.leaderboard-tab {
  min-width: 70px;
  border-radius: 9999px;
  border: 1px solid var(--line-1);
  background: var(--surface-3);
  padding: 7px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
  cursor: pointer;
  transition: all 150ms ease;
}
.leaderboard-tab:hover {
  border-color: var(--line-2);
  color: var(--ink-1);
}
.leaderboard-tab--active {
  border-color: transparent;
  background: var(--primary);
  color: #ffffff;
  box-shadow: 0 4px 12px rgba(0, 113, 227, 0.18);
}
.leaderboard-body {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding: 0 14px 10px;
  scrollbar-width: thin;
  scrollbar-color: var(--line-2) transparent;
}
.leaderboard-body::-webkit-scrollbar {
  width: 5px;
}
.leaderboard-body::-webkit-scrollbar-thumb {
  border-radius: 9999px;
  background: var(--line-2);
}

.leaderboard-podium {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: end;
  gap: 8px;
  margin: 2px 2px 10px;
  border: 1px solid var(--line-1);
  border-radius: 18px;
  background: linear-gradient(180deg, var(--primary-soft), rgba(0, 113, 227, 0.01));
  padding: 18px 8px 12px;
}
.leaderboard-podium__slot {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.leaderboard-podium__slot--1 {
  transform: translateY(-12px);
}
.leaderboard-crown {
  display: flex;
  height: 20px;
  align-items: center;
  justify-content: center;
  margin-bottom: 4px;
}
.leaderboard-crown svg {
  height: 17px;
  width: 25px;
}
.leaderboard-crown--gold {
  color: #d79a00;
}
.leaderboard-crown--silver {
  color: #98a2b3;
}
.leaderboard-crown--bronze {
  color: #b06b3f;
}
.leaderboard-crown--empty {
  color: var(--ink-3);
  font-size: 20px;
  font-weight: 700;
}
.leaderboard-podium__avatar {
  display: flex;
  height: 54px;
  width: 54px;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border-radius: 9999px;
  border: 2px solid var(--line-2);
  background: var(--surface-3);
  font-size: 19px;
  font-weight: 700;
  color: var(--primary);
}
.leaderboard-podium__avatar--first {
  height: 66px;
  width: 66px;
  border-color: #d79a00;
  box-shadow: 0 0 0 4px rgba(215, 154, 0, 0.10);
  font-size: 23px;
}
.leaderboard-podium__avatar img {
  height: 100%;
  width: 100%;
  object-fit: cover;
}
.leaderboard-podium__avatar--empty {
  border-style: dashed;
  color: var(--ink-3);
}
.leaderboard-podium__name {
  margin: 7px 0 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-1);
}
.leaderboard-podium__name--empty {
  color: var(--ink-3);
}
.leaderboard-podium__amount {
  margin: 2px 0 0;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--price);
}
.leaderboard-podium__amount--empty {
  color: var(--ink-3);
}

.leaderboard-ranklist {
  min-width: 0;
}
.leaderboard-ranklist__title {
  display: none;
}

.leaderboard-list {
  margin-top: 2px;
  border-top: 1px solid var(--line-1);
}
.leaderboard-row {
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--line-1);
  padding: 10px 8px;
}
.leaderboard-row:last-child {
  border-bottom: 0;
}
.leaderboard-row__rank {
  width: 20px;
  flex: none;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ink-3);
}
.leaderboard-row__avatar {
  display: flex;
  height: 34px;
  width: 34px;
  flex: none;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border-radius: 9999px;
  border: 1px solid var(--line-1);
  background: var(--surface-3);
  font-size: 13px;
  font-weight: 700;
  color: var(--primary);
}
.leaderboard-row__avatar img {
  height: 100%;
  width: 100%;
  object-fit: cover;
}
.leaderboard-row__name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-1);
}
.leaderboard-row__amount {
  flex: none;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--price);
}

.leaderboard-me {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 14px 14px;
  border-radius: 16px;
  border: 1px solid rgba(0, 113, 227, 0.16);
  background: linear-gradient(110deg, var(--primary-soft), rgba(0, 113, 227, 0.02));
  padding: 12px 14px;
}
.leaderboard-me__label {
  display: block;
  font-size: 11px;
  color: var(--ink-3);
}
.leaderboard-me__rank {
  display: block;
  margin-top: 2px;
  font-size: 22px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: var(--primary);
}
.leaderboard-me__right {
  text-align: right;
}
.leaderboard-me__gap {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}
.leaderboard-me__amount {
  margin: 3px 0 0;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--ink-3);
}

.leaderboard-loading {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 8px;
}
.leaderboard-skeleton {
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(90deg, var(--surface-3), var(--surface-2), var(--surface-3));
  background-size: 200% 100%;
  animation: leaderboard-shimmer 1.4s ease infinite;
}
.leaderboard-skeleton--podium {
  height: 150px;
}
@keyframes leaderboard-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.leaderboard-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 42px 16px;
  text-align: center;
}
.leaderboard-empty__icon {
  margin: 0;
  font-size: 34px;
}
.leaderboard-empty__title {
  margin: 10px 0 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink-1);
}
.leaderboard-empty__copy {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--ink-3);
}
.leaderboard-retry {
  margin-top: 14px;
  border-radius: 9999px;
  border: 1px solid var(--line-1);
  background: var(--surface-3);
  padding: 7px 16px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  cursor: pointer;
}
.leaderboard-retry:hover {
  background: var(--surface-2);
  color: var(--ink-1);
}

@media (min-width: 900px) {
  .leaderboard-panel {
    width: min(100%, 960px);
    max-height: min(86dvh, 760px);
  }
  .leaderboard-header {
    padding: 22px 24px 8px;
  }
  .leaderboard-title {
    font-size: 22px;
  }
  .leaderboard-subtitle {
    font-size: 13px;
  }
  .leaderboard-tabs {
    padding: 4px 24px 14px;
  }
  .leaderboard-body {
    display: grid;
    grid-template-columns: minmax(300px, 0.92fr) minmax(0, 1.08fr);
    gap: 24px;
    overflow: hidden;
    padding: 0 24px 14px;
  }
  .leaderboard-podium {
    align-self: stretch;
    align-content: center;
    margin: 0;
    padding: 24px 16px 20px;
  }
  .leaderboard-podium__avatar {
    height: 60px;
    width: 60px;
    font-size: 21px;
  }
  .leaderboard-podium__avatar--first {
    height: 78px;
    width: 78px;
    font-size: 25px;
  }
  .leaderboard-podium__name {
    font-size: 13px;
  }
  .leaderboard-podium__amount {
    font-size: 14px;
  }
  .leaderboard-ranklist {
    display: flex;
    min-height: 0;
    flex-direction: column;
  }
  .leaderboard-ranklist__title {
    display: block;
    flex: none;
    margin: 0;
    padding: 4px 8px 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--ink-3);
  }
  .leaderboard-list {
    min-height: 0;
    flex: 1;
    overflow-y: auto;
    margin-top: 0;
    border-top: 0;
    scrollbar-width: thin;
    scrollbar-color: var(--line-2) transparent;
  }
  .leaderboard-list::-webkit-scrollbar {
    width: 5px;
  }
  .leaderboard-list::-webkit-scrollbar-thumb {
    border-radius: 9999px;
    background: var(--line-2);
  }
  .leaderboard-row {
    padding: 12px 8px;
    border-radius: 10px;
  }
  .leaderboard-row:hover {
    background: var(--surface-2);
  }
  .leaderboard-me {
    margin: 0 24px 18px;
  }
  .leaderboard-empty,
  .leaderboard-loading {
    grid-column: 1 / -1;
  }
}

@media (max-width: 380px) {
  .leaderboard-panel {
    border-radius: 20px;
  }
  .leaderboard-header {
    padding: 18px 16px 8px;
  }
  .leaderboard-tabs {
    padding: 4px 16px 10px;
  }
  .leaderboard-me {
    margin: 0 10px 10px;
  }
}
</style>
