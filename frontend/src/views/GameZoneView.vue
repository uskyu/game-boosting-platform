<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useGamesStore } from '@/stores/games'
import { useSearchStore } from '@/stores/search'
import { formatPrice } from '@/utils/display'
import {
  buildAccentStyle,
  buildGameSurfaceStyle,
  getGamePlatformLabel,
  getGameServiceTypes,
} from '@/utils/gameCatalog'

const props = defineProps({
  id: {
    type: [String, Number],
    required: true,
  },
})

const route = useRoute()
const router = useRouter()
const gamesStore = useGamesStore()
const searchStore = useSearchStore()

const copy = {
  zoneName: '\u6e38\u620f\u4e13\u533a',
  loading: '\u6b63\u5728\u52a0\u8f7d\u6e38\u620f\u4fe1\u606f',
  description: '\u67e5\u770b\u8be5\u6e38\u620f\u7684\u6240\u6709\u53ef\u7528\u670d\u52a1\uff0c\u6309\u9700\u7b5b\u9009\u3002',
  serviceTypes: '\u670d\u52a1\u6a21\u5f0f',
  currentView: '\u5f53\u524d\u5185\u5bb9',
  orders: '\u8ba2\u5355',
  services: '\u670d\u52a1',
  availableNow: '\u5f53\u524d\u53ef\u4e0b\u5355',
  visibleServices: '\u53ef\u89c1\u670d\u52a1',
  noServiceShort: '\u6682\u65e0\u670d\u52a1',
  noServiceComing: '\u8be5\u6e38\u620f\u670d\u52a1\u5373\u5c06\u4e0a\u7ebf\uff0c\u656c\u8bf7\u671f\u5f85\u3002',
  allServices: '\u5168\u90e8\u670d\u52a1',
  pendingOrders: '\u5f85\u63a5\u8ba2\u5355',
  serviceItems: '\u5728\u7ebf\u670d\u52a1',
  requestPost: '\u9700\u6c42\u53d1\u5e03',
  waitingForBooster: '\u5df2\u53d1\u5e03\uff0c\u7b49\u5f85\u4ee3\u7ec3\u5e08\u63a5\u5355\u3002',
  serviceReady: '\u8fd9\u5f20\u670d\u52a1\u5361\u7247\u5df2\u51c6\u5907\u597d\u63a5\u5355\u3002',
  quote: '\u62a5\u4ef7',
  areaWaiting: '\u533a\u670d\u5f85\u6c9f\u901a',
  anonymous: '\u533f\u540d\u7528\u6237',
  dealCountPrefix: '\u6210\u4ea4 ',
  serviceUnavailable: '\u6682\u672a\u5f00\u653e',
  noContentTitle: '\u8be5\u6e38\u620f\u6682\u65f6\u6ca1\u6709\u5185\u5bb9',
  noContentCopy: '\u53ef\u4ee5\u5207\u6362\u670d\u52a1\u7c7b\u578b\uff0c\u6216\u7a0d\u540e\u518d\u56de\u6765\u67e5\u770b\u3002',
  pageInfoPrefix: '\u5f53\u524d\u7b2c ',
  pageInfoMiddle: ' / ',
  pageInfoSuffix: ' \u9875\uff0c\u5171 ',
  pageInfoEnd: ' \u6761\u7ed3\u679c',
  previous: '\u4e0a\u4e00\u9875',
  next: '\u4e0b\u4e00\u9875',
}

const activeTab = computed({
  get() {
    return route.query.tab === 'services' ? 'services' : 'orders'
  },
  set(value) {
    router.replace({
      query: {
        ...route.query,
        tab: value,
        page: undefined,
      },
    })
  },
})

const currentPage = computed(() => Number(route.query.page || 1))
const selectedServiceType = computed({
  get() {
    return route.query.service_type || ''
  },
  set(value) {
    router.replace({
      query: {
        ...route.query,
        service_type: value || undefined,
        page: undefined,
      },
    })
  },
})

const game = computed(() => gamesStore.currentGame)
const serviceTypes = computed(() => getGameServiceTypes(game.value))
const resultBlock = computed(() => {
  return activeTab.value === 'services' ? searchStore.servicesResult : searchStore.ordersResult
})

const visibleServiceCount = computed(() => {
  if (activeTab.value !== 'services') {
    return null
  }
  return Number(resultBlock.value.total || 0)
})

function openOrder(orderId) {
  router.push({ name: 'order-detail', params: { id: orderId } })
}

function openService(serviceId) {
  router.push({ name: 'service-detail', params: { id: serviceId } })
}

function setPage(page) {
  if (page < 1 || page > (resultBlock.value.pages || 0) || page === currentPage.value) {
    return
  }

  router.replace({
    query: {
      ...route.query,
      page,
    },
  })
}

async function runSearch() {
  await searchStore.search({
    q: '',
    type: activeTab.value,
    game_id: Number(props.id),
    service_type: selectedServiceType.value || '',
    page: currentPage.value,
    page_size: 12,
  })
}

watch(
  () => props.id,
  async (value) => {
    await gamesStore.fetchGame(value)
    await runSearch()
  },
  { immediate: true }
)

watch(
  () => [activeTab.value, selectedServiceType.value, currentPage.value],
  () => {
    runSearch()
  }
)

onMounted(() => {
  gamesStore.ensureCatalog()
})
</script>

<template>
  <div class="page-shell space-y-8">
    <section
      class="hero-panel scanline-overlay overflow-hidden p-6 sm:p-8 lg:p-10"
      :style="buildGameSurfaceStyle(game)"
    >
      <div class="absolute inset-0 bg-gradient-to-r from-slate-950/90 via-slate-950/78 to-slate-950/65"></div>

      <div class="relative z-10 grid gap-8 lg:grid-cols-[1.08fr_0.92fr] lg:items-end">
        <div class="space-y-4">
          <div class="flex flex-wrap items-center gap-3">
            <span class="eyebrow">{{ game?.name || copy.zoneName }}</span>
            <span class="tag">{{ getGamePlatformLabel(game?.platform) }}</span>
          </div>
          <h1 class="section-title neon-text !text-4xl sm:!text-5xl">
            {{ game?.name || copy.loading }}
          </h1>
          <p class="section-copy max-w-3xl">
            {{ copy.description }}
          </p>

          <div class="flex flex-wrap gap-2">
            <span
              v-for="serviceType in serviceTypes"
              :key="serviceType"
              class="tag"
            >
              {{ serviceType }}
            </span>
          </div>
        </div>

        <div class="grid gap-4 sm:grid-cols-3">
          <article class="stat-card cyber-corner">
            <p class="text-sm text-slate-400">{{ copy.serviceTypes }}</p>
            <p class="mt-2 text-3xl font-semibold text-white">{{ serviceTypes.length }}</p>
          </article>
          <article class="stat-card cyber-corner">
            <p class="text-sm text-slate-400">{{ copy.currentView }}</p>
            <p class="mt-2 text-3xl font-semibold text-white">{{ activeTab === 'orders' ? copy.orders : copy.services }}</p>
          </article>
          <article class="stat-card cyber-corner">
            <p class="text-sm text-slate-400">{{ copy.visibleServices }}</p>
            <template v-if="visibleServiceCount === 0">
              <p class="mt-2 text-3xl font-semibold text-white">{{ copy.noServiceShort }}</p>
              <p class="mt-2 text-xs leading-6 text-slate-400">{{ copy.noServiceComing }}</p>
            </template>
            <template v-else>
              <p class="mt-2 text-3xl font-semibold text-white">{{ resultBlock.total || 0 }}</p>
            </template>
          </article>
        </div>
      </div>
    </section>

    <section class="surface-card p-5 sm:p-6">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex flex-wrap gap-3">
          <button
            type="button"
            :class="activeTab === 'orders' ? 'tab-pill-active' : 'tab-pill'"
            @click="activeTab = 'orders'"
          >
            {{ copy.pendingOrders }}
          </button>
          <button
            type="button"
            :class="activeTab === 'services' ? 'tab-pill-active' : 'tab-pill'"
            @click="activeTab = 'services'"
          >
            {{ copy.serviceItems }}
          </button>
        </div>

        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            :class="selectedServiceType === '' ? 'filter-pill-active' : 'filter-pill'"
            @click="selectedServiceType = ''"
          >
            {{ copy.allServices }}
          </button>
          <button
            v-for="serviceType in serviceTypes"
            :key="serviceType"
            type="button"
            :class="selectedServiceType === serviceType ? 'filter-pill-active' : 'filter-pill'"
            @click="selectedServiceType = serviceType"
          >
            {{ serviceType }}
          </button>
        </div>
      </div>
    </section>

    <div v-if="searchStore.loading" class="grid gap-5" aria-busy="true">
      <div v-for="n in 4" :key="`zone-skeleton-${n}`" class="skeleton h-72 !rounded-card"></div>
    </div>

    <section v-else-if="activeTab === 'orders' && resultBlock.items.length" class="grid gap-5 xl:grid-cols-2">
      <button
        v-for="order in resultBlock.items"
        :key="`order-${order.id}`"
        type="button"
        class="catalog-card cyber-corner text-left"
        @click="openOrder(order.id)"
      >
        <div class="cover-card p-5" :style="buildGameSurfaceStyle(game)">
          <div class="relative z-10 flex h-full flex-col justify-between">
            <div class="flex items-center justify-between gap-4">
              <span class="tag">{{ order.service_type || copy.requestPost }}</span>
              <div
                class="flex h-11 w-11 items-center justify-center rounded-tile border text-sm font-semibold text-white"
                :style="buildAccentStyle(game)"
              >
                {{ order.game_name?.slice(0, 1) || '单' }}
              </div>
            </div>

            <div class="space-y-3">
              <h3 class="text-2xl font-semibold text-white">{{ order.current_rank }} → {{ order.target_rank }}</h3>
              <p class="text-sm leading-6 text-slate-300">{{ order.description_raw || copy.waitingForBooster }}</p>
            </div>
          </div>
        </div>

        <div class="mt-4 flex items-center justify-between gap-4">
          <div>
            <p class="text-xs uppercase tracking-[0.18em] text-slate-500">{{ copy.quote }}</p>
            <p class="mt-2 text-2xl font-semibold text-accent-300">{{ formatPrice(order.price) }}</p>
          </div>
          <div class="text-right text-sm text-slate-400">
            <p>{{ order.server || copy.areaWaiting }}</p>
            <p class="mt-2">{{ order.user?.username || copy.anonymous }}</p>
          </div>
        </div>
      </button>
    </section>

    <section v-else-if="activeTab === 'services' && resultBlock.items.length" class="grid gap-5 xl:grid-cols-3">
      <button
        v-for="service in resultBlock.items"
        :key="`service-${service.id}`"
        type="button"
        class="catalog-card cyber-corner text-left"
        @click="openService(service.id)"
      >
        <div class="cover-card p-5" :style="buildGameSurfaceStyle(game)">
          <div class="relative z-10 flex h-full flex-col justify-between">
            <div class="flex items-center justify-between gap-4">
              <span class="tag">{{ service.service_type }}</span>
              <div
                class="flex h-11 w-11 items-center justify-center rounded-tile border text-sm font-semibold text-white"
                :style="buildAccentStyle(game)"
              >
                服
              </div>
            </div>

            <div class="space-y-3">
              <h3 class="text-2xl font-semibold text-white">{{ service.title }}</h3>
              <p class="text-sm leading-6 text-slate-300">{{ service.description || copy.serviceReady }}</p>
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
            <p class="text-xs uppercase tracking-[0.18em] text-slate-500">每小时</p>
            <p class="mt-2 text-2xl font-semibold text-accent-300">{{ formatPrice(service.price_per_hour) }}</p>
          </div>
          <div class="text-right text-sm text-slate-400">
            <p>{{ copy.dealCountPrefix }}{{ service.order_count }} 单</p>
            <p class="mt-2">{{ service.is_available ? copy.availableNow : copy.serviceUnavailable }}</p>
          </div>
        </div>
      </button>
    </section>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🎯</div>
      <h2 class="empty-state__title">{{ copy.noContentTitle }}</h2>
      <p class="empty-state__copy">{{ copy.noContentCopy }}</p>
    </section>

    <section v-if="resultBlock.pages > 1" class="surface-card p-5">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm text-slate-400">
          {{ copy.pageInfoPrefix }}{{ resultBlock.page }}{{ copy.pageInfoMiddle }}{{ resultBlock.pages }}{{ copy.pageInfoSuffix }}{{ resultBlock.total }}{{ copy.pageInfoEnd }}
        </p>

        <div class="flex items-center gap-2">
          <button class="btn-secondary !px-4 !py-2" :disabled="currentPage <= 1" @click="setPage(currentPage - 1)">
            {{ copy.previous }}
          </button>
          <button class="btn-secondary !px-4 !py-2" :disabled="currentPage >= resultBlock.pages" @click="setPage(currentPage + 1)">
            {{ copy.next }}
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
