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

/**
 * 游戏专区（IA v2：/games，次级页面，权重低于订单大厅）。
 * 页面标题区收敛：eyebrow + 24–28px 标题 + 平台筛选，不做大 hero。
 */
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
  <div class="page-shell space-y-6">
    <!-- 页面标题区（收敛：eyebrow → 24–28px 标题 → 平台筛选同行） -->
    <section class="hero-panel p-6 sm:p-8">
      <div class="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div class="space-y-3">
          <p class="eyebrow">游戏专区</p>
          <h1 class="section-title">先选类型，再进专区</h1>
          <p class="section-copy max-w-2xl">按分类浏览游戏，点进去直接看订单和服务。</p>
        </div>

        <div class="scroll-x -mx-1 flex gap-2 px-1 lg:mx-0 lg:flex-wrap lg:px-0">
          <button
            v-for="option in platformOptions"
            :key="option.value || 'all'"
            type="button"
            :class="platformFilter === option.value ? 'filter-pill-active' : 'filter-pill'"
            class="shrink-0"
            @click="changePlatform(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
    </section>

    <!-- 分类网格 -->
    <section class="surface-card p-5 sm:p-6">
      <div class="flex items-center justify-between gap-4">
        <h2 class="text-[17px] font-semibold leading-6 text-ink-1">十大分类</h2>
        <p class="text-[13px] text-ink-3">已收录 {{ gamesStore.catalogGames.length }} 款</p>
      </div>

      <div class="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <button
          v-for="category in categories"
          :key="category.value"
          type="button"
          :class="activeCategory === category.value ? 'catalog-card-active text-left' : 'catalog-card text-left'"
          @click="changeCategory(category.value)"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-xs uppercase tracking-[0.16em] text-ink-3">{{ category.shortLabel }}</p>
              <h3 class="mt-2.5 text-[17px] font-semibold leading-6 text-ink-1">{{ category.label }}</h3>
            </div>
            <div
              class="flex h-10 w-10 shrink-0 items-center justify-center rounded-tile border text-sm font-semibold text-ink-1"
              :style="{ borderColor: `${category.accent}55`, background: `${category.accent}1a` }"
            >
              {{ category.icon }}
            </div>
          </div>
          <p class="mt-3 line-clamp-2 text-[13px] leading-6 text-ink-2">{{ category.description }}</p>
          <div class="mt-4 flex items-center justify-between border-t border-line-1 pt-3 text-[13px]">
            <span class="text-ink-3">已收录</span>
            <span class="font-semibold tabular-nums text-primary">{{ category.count }} 款</span>
          </div>
        </button>
      </div>
    </section>

    <!-- 当前分类下的游戏 -->
    <section v-if="selectedCategory" class="space-y-4">
      <div class="flex flex-col gap-2">
        <p class="eyebrow">{{ getGameCategoryMeta(selectedCategory.value).shortLabel }}</p>
        <h2 class="text-xl font-semibold leading-7 text-ink-1">{{ selectedCategory.label }} 下的游戏专区</h2>
      </div>

      <div v-if="loading" class="grid gap-5 md:grid-cols-2 xl:grid-cols-3" aria-busy="true">
        <div v-for="n in 6" :key="`cat-skeleton-${n}`" class="skeleton h-72 !rounded-card"></div>
      </div>

      <section v-else-if="selectedGames.length" class="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        <button
          v-for="game in selectedGames"
          :key="game.id"
          type="button"
          class="catalog-card text-left"
          @click="openGame(game.id)"
        >
          <div class="cover-card p-5" :style="buildGameSurfaceStyle(game)">
            <div class="relative z-10 flex h-full flex-col justify-between">
              <div class="flex items-start justify-between gap-4">
                <span class="tag">{{ getGamePlatformLabel(game.platform) }}</span>
                <div
                  class="flex h-10 w-10 items-center justify-center rounded-tile border text-sm font-semibold text-ink-1"
                  :style="buildAccentStyle(game)"
                >
                  {{ game.name.slice(0, 1) }}
                </div>
              </div>

              <div class="space-y-2">
                <h3 class="text-[17px] font-semibold leading-6 text-ink-1">{{ game.name }}</h3>
                <p class="text-xs text-ink-2">{{ game.english_name || '热门专区' }}</p>
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
