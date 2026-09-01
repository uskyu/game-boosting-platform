<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useGamesStore } from '@/stores/games'
import {
  buildAccentStyle,
  buildGameSurfaceStyle,
  getGameCategoryMeta,
  getGamePlatformLabel,
  getGameServiceTypes,
} from '@/utils/gameCatalog'

const route = useRoute()
const router = useRouter()
const gamesStore = useGamesStore()

const loading = computed(() => gamesStore.loading)
const categories = computed(() => gamesStore.categories)
const platformFilter = ref(route.query.platform || '')

const activeCategory = computed(() => {
  const queryCategory = route.query.category
  const firstAvailable = categories.value.find((item) => item.count > 0)?.value || categories.value[0]?.value
  return queryCategory || firstAvailable || ''
})

const selectedCategory = computed(() => {
  return categories.value.find((item) => item.value === activeCategory.value) || null
})

const selectedGames = computed(() => {
  const items = selectedCategory.value?.games || []
  if (!platformFilter.value) {
    return items
  }

  return items.filter((game) => game.platform === platformFilter.value)
})

const platformOptions = [
  { value: '', label: '全部平台' },
  { value: 'MOBILE', label: '手游' },
  { value: 'PC', label: '端游' },
  { value: 'BOTH', label: '双端' },
]

const categoryHighlights = computed(() => {
  return (selectedCategory.value?.games || []).slice(0, 3)
})

function changeCategory(category) {
  router.replace({
    query: {
      ...route.query,
      category,
    },
  })
}

function changePlatform(platform) {
  platformFilter.value = platform
  router.replace({
    query: {
      ...route.query,
      platform: platform || undefined,
    },
  })
}

function openGame(gameId) {
  router.push({ name: 'game-zone', params: { id: gameId } })
}

watch(
  () => route.query.platform,
  (value) => {
    platformFilter.value = value || ''
  }
)

onMounted(() => {
  gamesStore.ensureCatalog()
})
</script>

<template>
  <div class="page-shell space-y-8">
    <section class="hero-panel scanline-overlay p-6 sm:p-8 lg:p-10">
      <div class="grid gap-8 lg:grid-cols-[1.02fr_0.98fr] lg:items-end">
        <div class="space-y-4">
          <p class="eyebrow">游戏目录</p>
          <h1 class="section-title neon-text !text-4xl sm:!text-5xl">
            先选类型，再进专区。
          </h1>
          <p class="section-copy max-w-3xl">
            按类型看游戏。点进去，直接看订单和服务。
          </p>
        </div>

        <div class="grid gap-4 sm:grid-cols-3">
          <article class="stat-card cyber-corner">
            <p class="text-3xl font-semibold text-white">{{ categories.length }}</p>
            <p class="mt-2 text-sm text-slate-300">分类总数</p>
          </article>
          <article class="stat-card cyber-corner">
            <p class="text-3xl font-semibold text-white">{{ gamesStore.catalogGames.length }}</p>
            <p class="mt-2 text-sm text-slate-300">已上架游戏</p>
          </article>
          <article class="stat-card cyber-corner">
            <p class="text-3xl font-semibold text-white">{{ selectedGames.length }}</p>
            <p class="mt-2 text-sm text-slate-300">当前分类结果</p>
          </article>
        </div>
      </div>
    </section>

    <section class="surface-card p-5 sm:p-6">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p class="text-sm font-medium text-primary-100">10 大分类</p>
          <h2 class="mt-2 text-2xl font-semibold text-white">先挑赛道，再选游戏</h2>
        </div>

        <div class="flex flex-wrap gap-2">
          <button
            v-for="option in platformOptions"
            :key="option.value || 'all'"
            type="button"
            :class="platformFilter === option.value ? 'filter-pill-active' : 'filter-pill'"
            @click="changePlatform(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
      </div>

      <div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <button
          v-for="category in categories"
          :key="category.value"
          type="button"
          :class="activeCategory === category.value ? 'catalog-card-active cyber-corner text-left' : 'catalog-card cyber-corner text-left'"
          @click="changeCategory(category.value)"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-xs uppercase tracking-[0.22em] text-slate-500">{{ category.shortLabel }}</p>
              <h3 class="mt-3 text-xl font-semibold text-white">{{ category.label }}</h3>
              <p class="mt-3 text-sm leading-6 text-slate-400">{{ category.description }}</p>
            </div>
            <div
              class="flex h-12 w-12 items-center justify-center rounded-tile border text-sm font-semibold text-white"
              :style="{ borderColor: `${category.accent}55`, background: `${category.accent}1f`, boxShadow: `0 0 18px ${category.accent}22` }"
            >
              {{ category.icon }}
            </div>
          </div>
          <div class="mt-6 flex items-center justify-between text-sm">
            <span class="text-slate-500">已收录</span>
            <span class="font-semibold text-primary-100">{{ category.count }} 款</span>
          </div>
        </button>
      </div>
    </section>

    <section v-if="selectedCategory" class="space-y-6">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p class="eyebrow">{{ getGameCategoryMeta(selectedCategory.value).shortLabel }}</p>
          <h2 class="section-title mt-4">
            {{ selectedCategory.label }} 下的游戏专区
          </h2>
          <p class="section-copy mt-3 max-w-3xl">
            继续往下选。进入游戏后，直接看订单和服务。
          </p>
        </div>

        <div class="flex gap-3 overflow-x-auto">
          <article
            v-for="game in categoryHighlights"
            :key="`highlight-${game.id}`"
            class="info-tile !px-4 !py-3 text-sm text-slate-300"
          >
            <p class="font-semibold text-white">{{ game.name }}</p>
            <p class="mt-2 text-xs text-slate-500">{{ getGamePlatformLabel(game.platform) }}</p>
          </article>
        </div>
      </div>

      <div v-if="loading" class="grid gap-5 md:grid-cols-2 xl:grid-cols-3" aria-busy="true">
        <div v-for="n in 6" :key="`cat-skeleton-${n}`" class="skeleton h-72 !rounded-card"></div>
      </div>

      <section v-else-if="selectedGames.length" class="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        <button
          v-for="game in selectedGames"
          :key="game.id"
          type="button"
          class="catalog-card cyber-corner text-left"
          @click="openGame(game.id)"
        >
          <div class="cover-card p-5" :style="buildGameSurfaceStyle(game)">
            <div class="relative z-10 flex h-full flex-col justify-between">
              <div class="flex items-start justify-between gap-4">
                <span class="tag">{{ getGamePlatformLabel(game.platform) }}</span>
                <div
                  class="flex h-11 w-11 items-center justify-center rounded-tile border text-sm font-semibold text-white"
                  :style="buildAccentStyle(game)"
                >
                  {{ game.name.slice(0, 1) }}
                </div>
              </div>

              <div class="space-y-3">
                <div>
                  <h3 class="text-2xl font-semibold text-white">{{ game.name }}</h3>
                  <p class="mt-2 text-sm text-slate-300">{{ game.english_name || '热门专区' }}</p>
                </div>
                <p class="text-sm leading-6 text-slate-300">{{ game.description }}</p>
              </div>
            </div>
          </div>

          <div class="mt-4 flex flex-wrap gap-2">
            <span
              v-for="serviceType in getGameServiceTypes(game).slice(0, 3)"
              :key="`${game.id}-${serviceType}`"
              class="tag"
            >
              {{ serviceType }}
            </span>
          </div>
        </button>
      </section>

      <section v-else class="empty-state">
        <div class="empty-state__icon" aria-hidden="true">🕹️</div>
        <h3 class="empty-state__title">这个筛选下暂时没有游戏</h3>
        <p class="empty-state__copy">试着切换平台，或者回到其他分类继续浏览。</p>
      </section>
    </section>
  </div>
</template>
