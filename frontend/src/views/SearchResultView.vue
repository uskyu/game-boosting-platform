<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useGamesStore } from '@/stores/games'
import { useSearchStore } from '@/stores/search'
import { formatPrice } from '@/utils/display'
import {
  buildAccentStyle,
  buildGameSurfaceStyle,
  getGameCategoryMeta,
  getGamePlatformLabel,
} from '@/utils/gameCatalog'

const route = useRoute()
const router = useRouter()
const gamesStore = useGamesStore()
const searchStore = useSearchStore()

const searchQuery = ref(route.query.q || '')
const activeTab = ref(route.query.tab || (route.query.type === 'services' ? 'services' : 'orders'))
const selectedGameId = ref(route.query.game_id || '')
const selectedCategory = ref(route.query.category || '')
const selectedPlatform = ref(route.query.platform || '')
const selectedServiceType = ref(route.query.service_type || '')
const priceMin = ref(route.query.price_min || '')
const priceMax = ref(route.query.price_max || '')

const ordersResult = computed(() => searchStore.ordersResult)
const servicesResult = computed(() => searchStore.servicesResult)
const resultBlock = computed(() => (activeTab.value === 'services' ? servicesResult.value : ordersResult.value))

const currentGame = computed(() => {
  return selectedGameId.value ? gamesStore.getGameById(Number(selectedGameId.value)) : null
})

const availableServiceTypes = computed(() => {
  if (currentGame.value?.service_template?.service_types?.length) {
    return currentGame.value.service_template.service_types
  }

  return Array.from(
    new Set(
      gamesStore.catalogGames.flatMap((game) => game.service_template?.service_types || [])
    )
  )
})

function getGameById(id, fallbackName = '') {
  return gamesStore.getGameById(id) || {
    id,
    name: fallbackName || `游戏 #${id}`,
    description: '热门专区',
    platform: 'BOTH',
    color_theme: '#0071e3',
  }
}

async function runSearch(page = Number(route.query.page || 1), type = route.query.type || 'all') {
  await searchStore.search({
    q: searchQuery.value,
    type,
    game_id: selectedGameId.value || '',
    category: selectedCategory.value || '',
    platform: selectedPlatform.value || '',
    price_min: priceMin.value || '',
    price_max: priceMax.value || '',
    service_type: selectedServiceType.value || '',
    page,
    page_size: 12,
  })
}

function syncRoute(page = 1, type = route.query.type || 'all') {
  router.replace({
    query: {
      q: searchQuery.value || undefined,
      type,
      tab: activeTab.value,
      game_id: selectedGameId.value || undefined,
      category: selectedCategory.value || undefined,
      platform: selectedPlatform.value || undefined,
      price_min: priceMin.value || undefined,
      price_max: priceMax.value || undefined,
      service_type: selectedServiceType.value || undefined,
      page: page > 1 ? page : undefined,
    },
  })
}

function handleSearch() {
  syncRoute(1, route.query.type || 'all')
}

function handleTabChange(tab) {
  activeTab.value = tab
  const nextType = route.query.type === 'all' ? 'all' : tab
  syncRoute(1, nextType)
}

function setPage(page) {
  syncRoute(page, route.query.type || 'all')
}

function openOrder(orderId) {
  router.push({ name: 'order-detail', params: { id: orderId } })
}

function openService(serviceId) {
  router.push({ name: 'service-detail', params: { id: serviceId } })
}

watch(
  () => route.query,
  async (query) => {
    searchQuery.value = query.q || ''
    selectedGameId.value = query.game_id || ''
    selectedCategory.value = query.category || ''
    selectedPlatform.value = query.platform || ''
    selectedServiceType.value = query.service_type || ''
    priceMin.value = query.price_min || ''
    priceMax.value = query.price_max || ''
    activeTab.value = query.tab || (query.type === 'services' ? 'services' : 'orders')
    await runSearch(Number(query.page || 1), query.type || 'all')
  },
  { immediate: true }
)

onMounted(() => {
  gamesStore.ensureCatalog()
})
</script>

<template>
  <div class="page-shell space-y-8">
    <section class="hero-panel p-6 sm:p-8 lg:p-10">
      <div class="grid gap-8 lg:grid-cols-[1.08fr_0.92fr] lg:items-end">
        <div class="space-y-3">
          <p class="eyebrow">统一搜索</p>
          <h1 class="section-title">
            搜订单，也搜服务
          </h1>
          <p class="section-copy max-w-3xl">
            关键词、游戏、分类、价格都能一起筛。
          </p>
        </div>

        <div class="grid gap-4 sm:grid-cols-2">
          <article class="stat-card">
            <p class="text-[13px] text-ink-2">订单结果</p>
            <p class="stat-value mt-1.5 text-ink-1">{{ ordersResult.total || 0 }}</p>
          </article>
          <article class="stat-card">
            <p class="text-[13px] text-ink-2">服务结果</p>
            <p class="stat-value mt-1.5 text-ink-1">{{ servicesResult.total || 0 }}</p>
          </article>
        </div>
      </div>
    </section>

    <section class="surface-card p-5 sm:p-6">
      <div class="grid gap-4 lg:grid-cols-[1.2fr_1fr_1fr]">
        <div class="lg:col-span-3 search-shell">
          <input v-model="searchQuery" type="text" class="search-input" placeholder="搜索游戏名、需求、服务标题或标签" @keyup.enter="handleSearch" />
          <button class="btn-primary !px-5 !py-2.5" @click="handleSearch">开始搜索</button>
        </div>

        <div>
          <label class="label" for="search-game">游戏</label>
          <select id="search-game" v-model="selectedGameId" class="input">
            <option value="">全部游戏</option>
            <option v-for="game in gamesStore.catalogGames" :key="game.id" :value="game.id">{{ game.name }}</option>
          </select>
        </div>
        <div>
          <label class="label" for="search-category">分类</label>
          <select id="search-category" v-model="selectedCategory" class="input">
            <option value="">全部分类</option>
            <option
              v-for="category in gamesStore.categories"
              :key="category.value"
              :value="category.value"
            >
              {{ category.label }}
            </option>
          </select>
        </div>
        <div>
          <label class="label" for="search-platform">平台</label>
          <select id="search-platform" v-model="selectedPlatform" class="input">
            <option value="">全部平台</option>
            <option value="MOBILE">手游</option>
            <option value="PC">端游</option>
            <option value="BOTH">双端</option>
          </select>
        </div>

        <div>
          <label class="label" for="search-type">服务类型</label>
          <select id="search-type" v-model="selectedServiceType" class="input">
            <option value="">全部服务类型</option>
            <option
              v-for="serviceType in availableServiceTypes"
              :key="serviceType"
              :value="serviceType"
            >
              {{ serviceType }}
            </option>
          </select>
        </div>
        <div class="grid gap-4 sm:grid-cols-2 lg:col-span-2">
          <div>
            <label class="label" for="search-price-min">最低价格</label>
            <input id="search-price-min" v-model="priceMin" type="number" min="0" class="input" />
          </div>
          <div>
            <label class="label" for="search-price-max">最高价格</label>
            <input id="search-price-max" v-model="priceMax" type="number" min="0" class="input" />
          </div>
        </div>
      </div>
    </section>

    <section class="surface-card p-5 sm:p-6">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div class="flex flex-wrap gap-3">
          <button
            type="button"
            :class="activeTab === 'orders' ? 'tab-pill-active' : 'tab-pill'"
            @click="handleTabChange('orders')"
          >
            代练订单
          </button>
          <button
            type="button"
            :class="activeTab === 'services' ? 'tab-pill-active' : 'tab-pill'"
            @click="handleTabChange('services')"
          >
            陪玩服务
          </button>
        </div>

        <div class="text-sm text-ink-2">
          <span v-if="selectedCategory">{{ getGameCategoryMeta(selectedCategory).label }}</span>
          <span v-if="selectedPlatform"> · {{ getGamePlatformLabel(selectedPlatform) }}</span>
        </div>
      </div>
    </section>

    <div v-if="searchStore.loading" class="grid gap-5 xl:grid-cols-2" aria-busy="true">
      <div v-for="n in 4" :key="`search-skeleton-${n}`" class="skeleton h-72 !rounded-card"></div>
    </div>

    <div v-else-if="searchStore.error" class="message-error">
      {{ searchStore.error }}
    </div>

    <section v-else-if="activeTab === 'orders' && ordersResult.items.length" class="grid gap-5 xl:grid-cols-2">
      <button
        v-for="order in ordersResult.items"
        :key="`search-order-${order.id}`"
        type="button"
        class="catalog-card cyber-corner text-left"
        @click="openOrder(order.id)"
      >
        <div class="cover-card p-5" :style="buildGameSurfaceStyle(getGameById(order.game_id, order.game_name))">
          <div class="relative z-10 flex h-full flex-col justify-between">
            <div class="flex items-center justify-between gap-4">
              <span class="tag">{{ order.service_type || '订单需求' }}</span>
              <div class="text-right text-sm text-ink-2">
                <p>{{ order.game_name }}</p>
                <p class="mt-2">{{ order.server || '区服待沟通' }}</p>
              </div>
            </div>

            <div class="space-y-3">
              <h2 class="text-2xl font-semibold text-ink-1">{{ order.current_rank }} → {{ order.target_rank }}</h2>
              <p class="text-sm leading-6 text-ink-2">{{ order.description_raw || '已发布待接单。' }}</p>
            </div>
          </div>
        </div>

        <div class="mt-4 flex items-center justify-between gap-4">
          <div>
            <p class="text-xs uppercase tracking-[0.18em] text-ink-3">报价</p>
            <p class="mt-2 text-2xl font-semibold tabular-nums text-price">{{ formatPrice(order.price) }}</p>
          </div>
          <div
            class="flex h-11 w-11 items-center justify-center rounded-tile border text-sm font-semibold text-ink-1"
            :style="buildAccentStyle(getGameById(order.game_id, order.game_name))"
          >
            单
          </div>
        </div>
      </button>
    </section>

    <section v-else-if="activeTab === 'services' && servicesResult.items.length" class="grid gap-5 xl:grid-cols-3">
      <button
        v-for="service in servicesResult.items"
        :key="`search-service-${service.id}`"
        type="button"
        class="catalog-card cyber-corner text-left"
        @click="openService(service.id)"
      >
        <div class="cover-card p-5" :style="buildGameSurfaceStyle(getGameById(service.game_id))">
          <div class="relative z-10 flex h-full flex-col justify-between">
            <div class="flex items-center justify-between gap-4">
              <span class="tag">{{ service.service_type }}</span>
              <div
                class="flex h-11 w-11 items-center justify-center rounded-tile border text-sm font-semibold text-ink-1"
                :style="buildAccentStyle(getGameById(service.game_id))"
              >
                服
              </div>
            </div>

            <div class="space-y-3">
              <p class="text-sm text-ink-2">{{ getGameById(service.game_id).name }}</p>
              <h2 class="text-2xl font-semibold text-ink-1">{{ service.title }}</h2>
              <p class="text-sm leading-6 text-ink-2">{{ service.description || '已上架，支持从服务卡片直接下单。' }}</p>
            </div>
          </div>
        </div>

        <div class="mt-4 flex flex-wrap gap-2">
          <span
            v-for="tag in (service.tags || []).slice(0, 3)"
            :key="`${service.id}-${tag}`"
            class="tag"
          >
            {{ tag }}
          </span>
        </div>

        <div class="mt-4 flex items-center justify-between gap-4">
          <div>
            <p class="text-xs uppercase tracking-[0.18em] text-ink-3">每小时</p>
            <p class="mt-2 text-2xl font-semibold tabular-nums text-price">{{ formatPrice(service.price_per_hour) }}</p>
          </div>
          <div class="text-right text-sm text-ink-2">
            <p>{{ getGamePlatformLabel(getGameById(service.game_id).platform) }}</p>
            <p class="mt-2">成交 {{ service.order_count }} 单</p>
          </div>
        </div>
      </button>
    </section>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🔎</div>
      <h2 class="empty-state__title">没有找到匹配结果</h2>
      <p class="empty-state__copy">试着换一个关键词，或者放宽筛选条件再搜索一次。</p>
    </section>

    <section v-if="resultBlock.pages > 1" class="surface-card p-5">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm text-ink-2">
          当前第 {{ resultBlock.page }} / {{ resultBlock.pages }} 页，共 {{ resultBlock.total }} 条结果
        </p>

        <div class="flex items-center gap-2">
          <button class="btn-secondary !px-4 !py-2" :disabled="resultBlock.page <= 1" @click="setPage(resultBlock.page - 1)">
            上一页
          </button>
          <button class="btn-secondary !px-4 !py-2" :disabled="resultBlock.page >= resultBlock.pages" @click="setPage(resultBlock.page + 1)">
            下一页
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
