<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useOrdersStore } from '@/stores/orders'
import { formatPrice } from '@/utils/display'

/**
 * 指定接单打手选择器。
 * v-model：选中用户对象 { id, username, avatar_url, deposit_balance? }，未选中为 null。
 * 候选接口只返回昵称与保证金，页面也只展示这两类信息。
 */
const props = defineProps({
  modelValue: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits(['update:modelValue'])
const ordersStore = useOrdersStore()

const searchQuery = ref('')
const searchResults = ref([])
const recentCandidates = ref([])
const searchLoading = ref(false)
const recentLoading = ref(false)
const searchError = ref('')
const recentError = ref('')
const recentLoaded = ref(false)
const searchInput = ref(null)

let searchTimer = null
let searchSeq = 0
let recentSeq = 0

const selectedUser = computed(() => props.modelValue || null)
const trimmedQuery = computed(() => searchQuery.value.trim())
const isSearching = computed(() => trimmedQuery.value.length >= 2)
const activeCandidates = computed(() => (
  isSearching.value ? searchResults.value : recentCandidates.value
))
const activeLoading = computed(() => (
  isSearching.value ? searchLoading.value : recentLoading.value
))
const activeError = computed(() => (
  isSearching.value ? searchError.value : recentError.value
))
const activeEmptyText = computed(() => (
  isSearching.value
    ? '没有找到匹配的用户，试试输入更多字符。'
    : '暂无最近指定，搜索昵称或账号添加。'
))
const activeLoadingText = computed(() => (
  isSearching.value ? '正在搜索…' : '正在加载最近指定…'
))
// 搜索结果里有本人/已拉黑/已停接的候选时，明确说明为什么不能选，
// 避免出现「明明有这个人却提示没找到」的误导。
const blockedHint = computed(() => {
  const candidates = activeCandidates.value
  if (!candidates.length || candidates.some((item) => isAcceptable(item))) return ''
  if (candidates.some((item) => item.is_self)) {
    return '这是你自己，不能指定自己。'
  }
  if (candidates.some((item) => item.is_blacklisted)) {
    return '该用户在你的黑名单中，请先在黑名单页解除后再指定。'
  }
  if (candidates.some((item) => item.can_accept === false)) {
    return '该用户当前已停接，暂不能指定。'
  }
  return ''
})

function displayName(candidate) {
  if (candidate?.username) return candidate.username
  if (candidate?.id != null) return `用户 #${candidate.id}`
  return '用户'
}

function userInitial(candidate) {
  return displayName(candidate).slice(0, 1).toUpperCase()
}

function isAcceptable(candidate) {
  return candidate?.can_accept !== false
    && !candidate?.is_self
    && !candidate?.is_blacklisted
}

function hasDeposit(candidate) {
  return candidate?.deposit_balance != null
}

async function loadRecent({ force = false } = {}) {
  if (selectedUser.value) return
  if (recentLoading.value) return
  if (recentLoaded.value && !force) return

  const requestSeq = ++recentSeq
  recentLoading.value = true
  recentError.value = ''
  try {
    const result = await ordersStore.fetchDesignationCandidates('')
    if (requestSeq !== recentSeq) return
    if (!result.success) {
      recentCandidates.value = []
      recentError.value = result.error || '最近指定加载失败，请稍后重试'
      return
    }
    recentCandidates.value = Array.isArray(result.data?.recent) ? result.data.recent : []
    recentLoaded.value = true
  } finally {
    if (requestSeq === recentSeq) recentLoading.value = false
  }
}

async function runSearch(term) {
  const requestSeq = ++searchSeq
  searchLoading.value = true
  searchError.value = ''
  searchResults.value = []
  try {
    const result = await ordersStore.fetchDesignationCandidates(term)
    // 请求序号守卫：快速输入时旧响应不得覆盖新响应。
    if (requestSeq !== searchSeq) return
    if (!result.success) {
      searchError.value = result.error || '搜索失败，请稍后重试'
      return
    }
    searchResults.value = Array.isArray(result.data?.results) ? result.data.results : []
  } finally {
    if (requestSeq === searchSeq) searchLoading.value = false
  }
}

watch(searchQuery, (value) => {
  window.clearTimeout(searchTimer)
  searchTimer = null
  // 输入一变化就作废在飞搜索，避免旧关键词的结果回填到新关键词。
  searchSeq += 1
  searchLoading.value = false
  searchResults.value = []
  searchError.value = ''

  const term = value.trim()
  // 少于 2 个字符不发起搜索请求；最近指定只在挂载/更换时加载一次。
  if (term.length < 2) return
  // 防抖窗口内也显示「正在搜索…」，避免空结果文案提前闪一下
  searchLoading.value = true
  // 300ms 防抖：停顿后才真正请求，减少逐字输入造成的无效查询。
  searchTimer = window.setTimeout(() => {
    searchTimer = null
    runSearch(term)
  }, 300)
})

watch(selectedUser, (value) => {
  if (!value) {
    if (!recentLoaded.value) loadRecent()
    return
  }
  window.clearTimeout(searchTimer)
  searchTimer = null
  searchSeq += 1
  searchLoading.value = false
  searchQuery.value = ''
  searchResults.value = []
  searchError.value = ''
})

function selectCandidate(candidate) {
  if (!candidate || !isAcceptable(candidate)) return
  window.clearTimeout(searchTimer)
  searchTimer = null
  searchSeq += 1
  searchLoading.value = false
  searchQuery.value = ''
  searchResults.value = []
  searchError.value = ''
  emit('update:modelValue', {
    id: candidate.id,
    username: candidate.username,
    avatar_url: candidate.avatar_url ?? null,
    ...(candidate.deposit_balance != null
      ? { deposit_balance: candidate.deposit_balance }
      : {}),
  })
}

async function changeDesignatedUser() {
  emit('update:modelValue', null)
  recentLoaded.value = false
  searchQuery.value = ''
  searchResults.value = []
  searchError.value = ''
  await nextTick()
  searchInput.value?.focus()
  loadRecent({ force: true })
}

function retryActive() {
  if (isSearching.value) {
    const term = trimmedQuery.value
    if (term.length >= 2) runSearch(term)
    return
  }
  loadRecent({ force: true })
}

function handleEnter() {
  const candidate = activeCandidates.value.find((item) => isAcceptable(item))
  if (candidate) selectCandidate(candidate)
}

function handleEscape() {
  window.clearTimeout(searchTimer)
  searchTimer = null
  searchSeq += 1
  searchLoading.value = false
  searchQuery.value = ''
  searchResults.value = []
  searchError.value = ''
}

onMounted(() => {
  if (!selectedUser.value) loadRecent()
})

onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
  searchTimer = null
  searchSeq += 1
  recentSeq += 1
})
</script>

<template>
  <div class="designation-picker space-y-3">
    <!-- 已选中：只展示昵称与保证金（若有），并允许更换 -->
    <div v-if="selectedUser" class="rounded-tile border border-primary/40 bg-primary-soft p-3">
      <div class="flex items-center gap-3">
        <span
          v-if="!selectedUser.avatar_url"
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-soft text-sm font-semibold text-primary"
          aria-hidden="true"
        >{{ userInitial(selectedUser) }}</span>
        <img
          v-else
          :src="selectedUser.avatar_url"
          :alt="displayName(selectedUser)"
          loading="lazy"
          class="h-10 w-10 shrink-0 rounded-full border border-line-1 object-cover"
        />
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-semibold text-ink-1">{{ displayName(selectedUser) }}</p>
          <p
            v-if="hasDeposit(selectedUser)"
            class="mt-0.5 text-xs tabular-nums text-ink-2"
          >保证金 {{ formatPrice(selectedUser.deposit_balance) }}</p>
        </div>
        <button type="button" class="filter-pill shrink-0" @click="changeDesignatedUser">更换</button>
      </div>
      <p class="helper-text !mt-2 !text-primary">该打手将收到指定单，其他打手看不到也接不了。</p>
    </div>

    <!-- 未选中：搜索 + 最近指定 / 搜索结果内联列表（非浮层，移动端不被键盘遮挡） -->
    <template v-else>
      <div>
        <label class="label" for="designation-search">指定打手</label>
        <input
          id="designation-search"
          ref="searchInput"
          v-model="searchQuery"
          type="search"
          class="input"
          placeholder="搜索昵称或账号"
          autocomplete="off"
          @keydown.enter.prevent="handleEnter"
          @keydown.escape.prevent="handleEscape"
        />
        <p class="helper-text">输入至少 2 个字符搜索；只展示昵称与保证金。</p>
      </div>

      <div class="rounded-tile border border-line-1 bg-surface-2" aria-live="polite">
        <div class="flex items-center justify-between gap-2 border-b border-line-1 px-3 py-2">
          <p class="text-xs font-semibold text-ink-2">{{ isSearching ? '搜索结果' : '最近指定' }}</p>
          <span v-if="activeLoading" class="text-xs text-ink-3">{{ activeLoadingText }}</span>
        </div>

        <div v-if="activeLoading" class="px-3 py-4 text-sm text-ink-3">{{ activeLoadingText }}</div>

        <div v-else-if="activeError" class="flex flex-wrap items-center gap-2 px-3 py-4">
          <p class="min-w-0 flex-1 text-sm text-danger">{{ activeError }}</p>
          <button type="button" class="filter-pill shrink-0" @click="retryActive">重试</button>
        </div>

        <p v-else-if="!activeCandidates.length" class="px-3 py-4 text-sm text-ink-3">{{ activeEmptyText }}</p>

        <div v-else class="max-h-[280px] overflow-y-auto p-1">
          <button
            v-for="candidate in activeCandidates"
            :key="candidate.id"
            type="button"
            class="flex w-full items-center gap-3 rounded-tile px-2 py-2 text-left transition-colors duration-base ease-smooth hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-60"
            :disabled="!isAcceptable(candidate)"
            @click="selectCandidate(candidate)"
          >
            <span
              v-if="!candidate.avatar_url"
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary"
              aria-hidden="true"
            >{{ userInitial(candidate) }}</span>
            <img
              v-else
              :src="candidate.avatar_url"
              :alt="displayName(candidate)"
              loading="lazy"
              class="h-9 w-9 shrink-0 rounded-full border border-line-1 object-cover"
            />
            <span class="min-w-0 flex-1">
              <span class="flex flex-wrap items-center gap-1.5">
                <span class="truncate text-sm font-semibold text-ink-1">{{ displayName(candidate) }}</span>
                <span v-if="candidate.is_self" class="tag !bg-surface-3 !text-ink-2">本人</span>
                <span v-else-if="candidate.is_blacklisted" class="tag !bg-danger-soft !text-danger">已拉黑</span>
                <span v-else-if="candidate.can_accept === false" class="tag !bg-surface-3 !text-ink-2">已停接</span>
              </span>
              <span
                v-if="hasDeposit(candidate)"
                class="mt-0.5 block text-xs tabular-nums text-ink-3"
              >保证金 {{ formatPrice(candidate.deposit_balance) }}</span>
            </span>
            <span v-if="isAcceptable(candidate)" class="shrink-0 text-xs font-medium text-primary">选择</span>
          </button>
        </div>
        <p v-if="blockedHint" class="border-t border-line-1 px-3 py-2 text-xs leading-5 text-ink-2">
          {{ blockedHint }}
        </p>
      </div>
    </template>
  </div>
</template>
