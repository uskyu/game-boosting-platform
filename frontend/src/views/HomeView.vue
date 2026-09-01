<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import HomeHeroCanvas from '@/components/home/HomeHeroCanvas.vue'
import { getGameHeroPool, PAGE_BACKGROUNDS } from '@/data/gameImages'
import { useGamesStore } from '@/stores/games'
import { useServicesStore } from '@/stores/services'
import { formatPrice } from '@/utils/display'
import {
  buildGameSurfaceStyle,
  getGamePlatformLabel,
  getGameServiceTypes,
  resolveGameVisual,
} from '@/utils/gameCatalog'
import { getTimeGreeting } from '@/utils/humanCopy'

const router = useRouter()
const gamesStore = useGamesStore()
const servicesStore = useServicesStore()

const copy = {
  fallbackCategory: '\u9009\u62e9\u6218\u573a',
  fallbackHeroTitle: '\u8fdb\u5165\u4f60\u7684\u4e0b\u4e00\u573a\u6e38\u620f',
  heroSubtitleDefault: '\u70ed\u95e8\u670d\u52a1\u4e13\u533a \uff5c \u4ee3\u7ec3 \u00b7 \u966a\u73a9 \u00b7 \u6559\u5b66',
  enterPrefix: '\u8fdb\u5165 ',
  modeButton: '我要…',
  detailLabel: '\u6e38\u620f\u8be6\u60c5',
  platform: '\u5e73\u53f0',
  modes: '\u6a21\u5f0f',
  services: '\u53ef\u7528\u670d\u52a1',
  noServiceShort: '\u6682\u65e0\u670d\u52a1',
  searchButton: '搜一下',
  searchPrefix: '在 ',
  searchSuffix: ' 里搜游戏或服务',
  chooseMode: '\u9009\u62e9\u6a21\u5f0f',
  modeCopy: '\u9009\u62e9\u4f60\u9700\u8981\u7684\u670d\u52a1\u7c7b\u578b\uff0c\u76f4\u63a5\u8fdb\u5165\u5bf9\u5e94\u4e13\u533a\u3002',
  boostLabel: '帮我上分',
  playLabel: '一起玩',
  coachLabel: '带我学',
  openModePrefix: '去找',
  currentFocus: '\u5f53\u524d\u7126\u70b9',
  focusCopy: '\u67e5\u770b\u8be5\u6e38\u620f\u7684\u6240\u6709\u53ef\u7528\u670d\u52a1\uff0c\u6309\u9700\u7b5b\u9009\u3002',
  visibleServices: '\u53ef\u89c1\u670d\u52a1',
  noServiceComing: '\u8be5\u6e38\u620f\u670d\u52a1\u5373\u5c06\u4e0a\u7ebf\uff0c\u656c\u8bf7\u671f\u5f85\u3002',
  noSampleTitle: '\u8be5\u6e38\u620f\u6682\u65e0\u5728\u7ebf\u670d\u52a1',
  noSampleCopy: '\u4f60\u53ef\u4ee5\u8fdb\u5165\u4e13\u533a\u67e5\u770b\u8be6\u60c5\uff0c\u6216\u5207\u6362\u5176\u4ed6\u6e38\u620f\u3002',
  serviceFallback: '\u8fd9\u6761\u670d\u52a1\u53ef\u4ee5\u76f4\u63a5\u8fdb\u5165\uff0c\u4e0d\u9700\u8981\u5148\u7ecf\u8fc7\u72ec\u7acb\u76ee\u5f55\u9875\u3002',
  dealsPrefix: '\u6210\u4ea4 ',
  cardServiceSuffix: ' \u9879\u670d\u52a1',
  currentGame: '\u5f53\u524d\u6e38\u620f',
  carouselPrev: '\u4e0a\u4e00\u7ec4',
  carouselNext: '\u4e0b\u4e00\u7ec4',
}

const heroSubtitleByName = {
  ['王者荣耀']: '国民级 5v5 MOBA ｜ 代练 · 陪玩 · 教学',
  ['英雄联盟']: '经典 5v5 MOBA ｜ 代练 · 陪玩 · 教学',
  ['英雄联盟手游']: '竞技 MOBA ｜ 代练 · 陪玩 · 教学',
  ['DOTA2']: '经典 MOBA ｜ 代练 · 陪玩 · 教学',
  ['曙光英雄']: '节奏对抗 MOBA ｜ 代练 · 陪玩 · 教学',
  ['决战！平安京']: '和风 MOBA ｜ 代练 · 陪玩 · 教学',
  ['原神']: '开放世界冒险 RPG ｜ 代肝 · 陪玩 · 教学',
  ['无畏契约']: '战术竞技 FPS ｜ 代练 · 陪玩 · 教学',
  ['三角洲行动']: '拟真军事 FPS ｜ 代练 · 陪玩 · 教学',
  ['金铲铲之战']: '自走棋策略 ｜ 代练 · 陪玩 · 教学',
}

const activeCategory = ref('')
const activeGameId = ref(null)
const activeModeKey = ref('boost')
const quickQuery = ref('')
const showCategoryPanel = ref(false)
const showGamePanel = ref(false)
const carouselRef = ref(null)
const progressTrackRef = ref(null)
const isDragging = ref(false)
const dragMoved = ref(false)
const carouselProgress = ref({
  thumbWidth: 26,
  thumbLeft: 0,
})

const greeting = computed(() => getTimeGreeting())
const slideshowHero = ref(null)
let slideshowTimer = null

let dragStartX = 0
let dragStartScrollLeft = 0
let hasSeededSelection = false
let isSeekingProgress = false
let pointerStartGameId = null

const categories = computed(() => gamesStore.categories.filter((item) => item.count > 0))

const activeCategoryRecord = computed(() => {
  return categories.value.find((item) => item.value === activeCategory.value) || categories.value[0] || null
})

const heroGames = computed(() => {
  const scoped = activeCategoryRecord.value?.games || []
  if (scoped.length) {
    return scoped.slice(0, 12)
  }

  return gamesStore.catalogGames.slice(0, 12)
})

const activeGame = computed(() => {
  if (!heroGames.value.length) {
    return gamesStore.randomGame || null
  }

  return heroGames.value.find((game) => game.id === activeGameId.value) || heroGames.value[0]
})

const heroVisual = computed(() => {
  const base = resolveGameVisual(activeGame.value)
  if (slideshowHero.value) {
    return { ...base, hero: slideshowHero.value }
  }
  return base
})
const heroAccent = computed(() => heroVisual.value.color || '#ff4655')
const serviceTypes = computed(() => getGameServiceTypes(activeGame.value))

const serviceCountByGame = computed(() => {
  return servicesStore.services.reduce((result, service) => {
    const gameId = Number(service.game_id)
    result[gameId] = (result[gameId] || 0) + 1
    return result
  }, {})
})

const selectedGameServices = computed(() => {
  if (!activeGame.value) {
    return []
  }

  return servicesStore.services
    .filter((service) => service.game_id === activeGame.value.id)
    .slice(0, 3)
})

const heroSubtitle = computed(() => {
  if (!activeGame.value) {
    return copy.heroSubtitleDefault
  }

  return heroSubtitleByName[activeGame.value.name] || `${getGamePlatformLabel(activeGame.value.platform)} \uff5c \u4ee3\u7ec3 \u00b7 \u966a\u73a9 \u00b7 \u6559\u5b66`
})

const heroStats = computed(() => [
  {
    label: copy.platform,
    value: getGamePlatformLabel(activeGame.value?.platform),
  },
  {
    label: copy.modes,
    value: serviceTypes.value.length ? `${serviceTypes.value.length} \u79cd` : '--',
  },
  {
    label: copy.services,
    value: (serviceCountByGame.value[activeGame.value?.id] || 0) > 0
      ? `${serviceCountByGame.value[activeGame.value?.id]} \u9879`
      : copy.noServiceShort,
  },
])

const focusServiceCount = computed(() => serviceCountByGame.value[activeGame.value?.id] || 0)

const modeCards = computed(() => [
  {
    key: 'boost',
    label: copy.boostLabel,
    serviceType: resolveModeType(['\u4ee3', '\u4e0a\u5206'], ['boost', 'rank']),
  },
  {
    key: 'play',
    label: copy.playLabel,
    serviceType: resolveModeType(['\u966a'], ['duo', 'party', 'play']),
  },
  {
    key: 'coach',
    label: copy.coachLabel,
    serviceType: resolveModeType(['\u6559'], ['coach', 'review', 'guide']),
  },
])

const activeModeCard = computed(() => {
  return modeCards.value.find((item) => item.key === activeModeKey.value) || modeCards.value[0]
})

const heroThemeStyle = computed(() => ({
  '--brand-rgb': toRgbTriplet(heroAccent.value),
}))

function toRgbTriplet(hex) {
  const safe = String(hex || '').replace('#', '').trim()
  if (safe.length !== 6) {
    return '255, 70, 85'
  }

  const red = Number.parseInt(safe.slice(0, 2), 16)
  const green = Number.parseInt(safe.slice(2, 4), 16)
  const blue = Number.parseInt(safe.slice(4, 6), 16)
  return `${red}, ${green}, ${blue}`
}

function resolveModeType(chineseKeywords = [], englishKeywords = []) {
  const types = serviceTypes.value
  if (!types.length) {
    return ''
  }

  const lowerKeywords = englishKeywords.map((item) => item.toLowerCase())

  const matched = types.find((type) => {
    const content = String(type || '')
    const lower = content.toLowerCase()
    return chineseKeywords.some((keyword) => content.includes(keyword))
      || lowerKeywords.some((keyword) => lower.includes(keyword))
  })

  return matched || types[0] || ''
}

function seedRandomSelection() {
  const availableCategories = categories.value.filter((item) => item.games?.length)
  if (!availableCategories.length) {
    return
  }

  // 首次进入默认选中和平精英；用户仍可切换到其他游戏看对应封面。
  const peacekeeperCategory = availableCategories.find((item) =>
    item.games.some((game) => game.name === '和平精英'),
  )
  if (peacekeeperCategory) {
    const peacekeeperGame = peacekeeperCategory.games.find((game) => game.name === '和平精英')
    activeCategory.value = peacekeeperCategory.value
    activeGameId.value = peacekeeperGame?.id || null
    return
  }

  const category = availableCategories[Math.floor(Math.random() * availableCategories.length)]
  const scopedGames = category.games.slice(0, 12)
  const randomGame = scopedGames[Math.floor(Math.random() * scopedGames.length)] || scopedGames[0]

  activeCategory.value = category.value
  activeGameId.value = randomGame?.id || null
}

function ensureSelection() {
  if (!categories.value.length) {
    activeCategory.value = ''
    activeGameId.value = null
    return
  }

  if (!categories.value.some((item) => item.value === activeCategory.value)) {
    activeCategory.value = categories.value[0].value
  }

  if (!heroGames.value.some((game) => game.id === activeGameId.value)) {
    activeGameId.value = heroGames.value[0]?.id || null
  }
}

function selectCategory(category) {
  if (category === activeCategory.value) {
    scrollToGame(activeGameId.value, 'smooth')
    return
  }

  activeCategory.value = category
  activeGameId.value = categories.value.find((item) => item.value === category)?.games?.[0]?.id || null
  nextTick(() => {
    scrollToGame(activeGameId.value, 'smooth')
    updateCarouselMetrics()
  })
}

function setActiveGame(gameId, options = {}) {
  if (!gameId) {
    return
  }

  activeGameId.value = gameId
  if (options.scroll !== false) {
    nextTick(() => {
      scrollToGame(gameId, options.behavior || 'smooth')
    })
  }
}

function setActiveMode(modeKey) {
  activeModeKey.value = modeKey
}

function handleGameCardClick(gameId) {
  if (dragMoved.value) {
    dragMoved.value = false
    return
  }

  setActiveGame(gameId)
}

function handleGameCardKeydown(event, gameId) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    setActiveGame(gameId)
  }
}

function openGameZone() {
  if (!activeGame.value) {
    return
  }

  router.push({ name: 'game-zone', params: { id: activeGame.value.id } })
}

function openServiceMode(serviceType) {
  if (!activeGame.value) {
    return
  }

  router.push({
    name: 'game-zone',
    params: { id: activeGame.value.id },
    query: {
      tab: 'services',
      service_type: serviceType || undefined,
    },
  })
}

function openActiveMode() {
  openServiceMode(activeModeCard.value?.serviceType)
}

function openService(serviceId) {
  router.push({ name: 'service-detail', params: { id: serviceId } })
}

function scrollToModes() {
  document.querySelector('#match-floor')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function submitQuickSearch() {
  router.push({
    name: 'search',
    query: {
      q: quickQuery.value.trim() || undefined,
      type: 'all',
      game_id: activeGame.value?.id || undefined,
    },
  })
}

function scrollToGame(gameId, behavior = 'smooth') {
  const rail = carouselRef.value
  if (!rail || !gameId) {
    return
  }

  const card = rail.querySelector(`[data-game-id="${gameId}"]`)
  if (!card) {
    return
  }

  const left = card.offsetLeft - (rail.clientWidth - card.clientWidth) / 2
  rail.scrollTo({
    left: Math.max(0, left),
    behavior,
  })
}

function scrollCarousel(direction) {
  if (!heroGames.value.length || !activeGame.value) {
    return
  }

  const currentIndex = heroGames.value.findIndex((game) => game.id === activeGame.value.id)
  const fallbackIndex = currentIndex === -1 ? 0 : currentIndex
  const nextIndex = Math.min(Math.max(fallbackIndex + direction, 0), heroGames.value.length - 1)
  const nextGame = heroGames.value[nextIndex]

  if (nextGame) {
    setActiveGame(nextGame.id)
  }
}

function handleCarouselPointerDown(event) {
  if (event.pointerType === 'mouse' && event.button !== 0) {
    return
  }

  const rail = carouselRef.value
  if (!rail) {
    return
  }

  isDragging.value = true
  dragMoved.value = false
  dragStartX = event.clientX
  dragStartScrollLeft = rail.scrollLeft
  pointerStartGameId = Number(event.target?.closest?.('[data-game-id]')?.getAttribute('data-game-id')) || null
  rail.setPointerCapture?.(event.pointerId)
}

function handleCarouselPointerMove(event) {
  if (!isDragging.value) {
    return
  }

  const rail = carouselRef.value
  if (!rail) {
    return
  }

  const delta = event.clientX - dragStartX
  if (Math.abs(delta) > 6) {
    dragMoved.value = true
  }

  rail.scrollLeft = dragStartScrollLeft - delta
  updateCarouselMetrics()
}

function handleCarouselPointerEnd(event) {
  if (!isDragging.value) {
    return
  }

  isDragging.value = false
  carouselRef.value?.releasePointerCapture?.(event.pointerId)
  if (!dragMoved.value && pointerStartGameId) {
    setActiveGame(pointerStartGameId)
  }
  pointerStartGameId = null
  window.setTimeout(() => {
    dragMoved.value = false
  }, 0)
}

function updateCarouselMetrics() {
  const rail = carouselRef.value
  if (!rail) {
    return
  }

  const maxScroll = Math.max(rail.scrollWidth - rail.clientWidth, 0)
  const thumbWidth = rail.scrollWidth <= rail.clientWidth
    ? 100
    : Math.max((rail.clientWidth / rail.scrollWidth) * 100, 14)
  const thumbTravel = 100 - thumbWidth
  const ratio = maxScroll === 0 ? 0 : rail.scrollLeft / maxScroll

  carouselProgress.value = {
    thumbWidth,
    thumbLeft: thumbTravel * ratio,
  }
}

function updateCarouselFromProgress(event) {
  const rail = carouselRef.value
  const track = progressTrackRef.value
  if (!rail || !track) {
    return
  }

  const rect = track.getBoundingClientRect()
  const position = (event.clientX - rect.left) / rect.width
  const clamped = Math.min(Math.max(position, 0), 1)
  const maxScroll = Math.max(rail.scrollWidth - rail.clientWidth, 0)
  rail.scrollLeft = maxScroll * clamped
  updateCarouselMetrics()
}

function handleProgressPointerDown(event) {
  const track = progressTrackRef.value
  if (!track) {
    return
  }

  isSeekingProgress = true
  track.setPointerCapture?.(event.pointerId)
  updateCarouselFromProgress(event)
}

function handleProgressPointerMove(event) {
  if (!isSeekingProgress) {
    return
  }

  updateCarouselFromProgress(event)
}

function handleProgressPointerEnd(event) {
  if (!isSeekingProgress) {
    return
  }

  isSeekingProgress = false
  progressTrackRef.value?.releasePointerCapture?.(event.pointerId)
}

function startSlideshow() {
  stopSlideshow()
  const gameName = activeGame.value?.name
  const pool = gameName ? getGameHeroPool(gameName) : []
  if (pool.length <= 1) {
    slideshowHero.value = null
    return
  }

  slideshowHero.value = pool[Math.floor(Math.random() * pool.length)]

  slideshowTimer = window.setInterval(() => {
    const current = slideshowHero.value
    let next = current
    while (next === current) {
      next = pool[Math.floor(Math.random() * pool.length)]
    }
    slideshowHero.value = next
  }, 30000)
}

function stopSlideshow() {
  if (slideshowTimer) {
    window.clearInterval(slideshowTimer)
    slideshowTimer = null
  }
}

watch(categories, async (items) => {
  if (!items.length) {
    return
  }

  if (!hasSeededSelection) {
    seedRandomSelection()
    hasSeededSelection = true
    await nextTick()
    scrollToGame(activeGameId.value, 'auto')
    updateCarouselMetrics()
    return
  }

  ensureSelection()
  await nextTick()
  updateCarouselMetrics()
}, { immediate: true })

watch(heroGames, async () => {
  ensureSelection()
  await nextTick()
  updateCarouselMetrics()
})

watch(activeGame, async () => {
  await nextTick()
  updateCarouselMetrics()
  startSlideshow()
})

onMounted(async () => {
  await Promise.all([
    gamesStore.ensureCatalog(),
    servicesStore.fetchServices({
      page: 1,
      page_size: 60,
      game_id: '',
      service_type: '',
      price_min: '',
      price_max: '',
    }),
  ])

  await nextTick()
  updateCarouselMetrics()
  window.addEventListener('resize', updateCarouselMetrics)
  startSlideshow()
})

onBeforeUnmount(() => {
  stopSlideshow()
  window.removeEventListener('resize', updateCarouselMetrics)
})
</script>

<template>
  <div class="home-landing">
    <section class="home-hero" :style="heroThemeStyle">
      <transition name="hero-fade" mode="out-in">
        <div
          :key="heroVisual.hero || activeGame?.id || activeCategory"
          class="home-hero__media"
          :style="{ backgroundImage: `url('${heroVisual.hero || PAGE_BACKGROUNDS.hero}')` }"
        ></div>
      </transition>

      <HomeHeroCanvas :accent="heroAccent" />
      <div class="home-hero__veil"></div>

      <div class="home-hero__inner shell-container">
        <div class="home-hero__copy space-y-6">
          <p class="home-kicker">{{ activeCategoryRecord?.label || copy.fallbackCategory }}</p>
          <p class="text-sm font-medium text-primary-200 mb-2 tracking-wide">{{ greeting }}</p>
          <h1 class="home-headline">{{ activeGame?.name || copy.fallbackHeroTitle }}</h1>
          <p class="home-summary">{{ heroSubtitle }}</p>

          <div class="home-actions">
            <button type="button" class="btn-primary" @click="openGameZone">
              {{ copy.enterPrefix }}{{ activeGame?.name || '' }}
            </button>
            <button type="button" class="btn-secondary" @click="scrollToModes">
              {{ copy.modeButton }}
            </button>
          </div>

          <div class="home-reveal-row">
            <button
              type="button"
              :class="['home-reveal-toggle', { 'home-reveal-toggle-active': showCategoryPanel }]"
              :aria-label="showCategoryPanel ? '收起分类' : '展开分类'"
              :aria-expanded="showCategoryPanel"
              @click="showCategoryPanel = !showCategoryPanel"
            >
              <span>•••</span>
            </button>
            <div :class="['home-reveal-panel', { 'is-open': showCategoryPanel }]">
              <div class="home-category-row">
                <button
                  v-for="category in categories"
                  :key="category.value"
                  type="button"
                  :class="category.value === activeCategory ? 'home-category-pill home-category-pill-active' : 'home-category-pill'"
                  @click="selectCategory(category.value)"
                >
                  <span>{{ category.label }}</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="home-reveal-row">
          <button
            type="button"
            :class="['home-reveal-toggle', { 'home-reveal-toggle-active': showGamePanel }]"
            :aria-label="showGamePanel ? '收起游戏列表' : '展开游戏列表'"
            :aria-expanded="showGamePanel"
            @click="showGamePanel = !showGamePanel"
          >
            <span>•••</span>
          </button>
          <div :class="['home-reveal-panel', { 'is-open': showGamePanel }]">
            <div
              ref="carouselRef"
              :class="isDragging ? 'home-game-row is-dragging' : 'home-game-row'"
              @pointerdown="handleCarouselPointerDown"
              @pointermove="handleCarouselPointerMove"
              @pointerup="handleCarouselPointerEnd"
              @pointercancel="handleCarouselPointerEnd"
              @pointerleave="handleCarouselPointerEnd"
              @scroll="updateCarouselMetrics"
            >
              <button
                v-for="game in heroGames"
                :key="game.id"
                :data-game-id="game.id"
                type="button"
                :class="game.id === activeGame?.id ? 'home-game-pill home-game-pill-active' : 'home-game-pill'"
                @click="handleGameCardClick(game.id)"
                @keydown="handleGameCardKeydown($event, game.id)"
              >
                <span>{{ game.name }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section id="match-floor" class="shell-container py-14 sm:py-16 lg:py-20">
      <div class="grid gap-10 lg:grid-cols-[0.88fr_1.12fr] lg:items-start">
        <div class="space-y-4">
          <p class="eyebrow">{{ copy.chooseMode }}</p>
          <h2 class="section-title !text-4xl sm:!text-5xl">{{ copy.chooseMode }}</h2>
          <p class="section-copy max-w-xl">{{ copy.modeCopy }}</p>
        </div>

        <div class="home-mode-stage">
          <div class="home-mode-stage__panel">
            <div class="flex items-start justify-between gap-4">
              <div>
                <p class="text-xs uppercase tracking-[0.24em] text-slate-500">{{ activeGame?.name || copy.currentGame }}</p>
                <h3 class="mt-4 text-4xl font-semibold text-white">{{ activeModeCard?.label }}</h3>
              </div>
              <span class="tag">{{ getGamePlatformLabel(activeGame?.platform) }}</span>
            </div>

            <div class="mt-8 flex flex-wrap gap-3">
              <span
                v-for="serviceType in serviceTypes.slice(0, 3)"
                :key="`mode-${serviceType}`"
                class="tag"
              >
                {{ serviceType }}
              </span>
            </div>

            <button type="button" class="btn-primary mt-8" @click="openActiveMode">
              {{ copy.openModePrefix }}{{ activeModeCard?.label }}
            </button>
          </div>

          <div class="home-mode-stage__selectors">
            <button
              v-for="mode in modeCards"
              :key="mode.key"
              type="button"
              :class="mode.key === activeModeKey ? 'home-mode-selector home-mode-selector-active' : 'home-mode-selector'"
              @click="setActiveMode(mode.key)"
            >
              <span class="home-mode-selector__title">{{ mode.label }}</span>
            </button>
          </div>
        </div>
      </div>
    </section>

    <section class="shell-container pb-16 sm:pb-20 lg:pb-24">
      <div class="grid gap-6 lg:grid-cols-[1.08fr_0.92fr]">
        <article class="home-spotlight" :style="buildGameSurfaceStyle(activeGame)">
          <div class="relative z-10 flex h-full flex-col justify-between gap-6">
            <div class="space-y-3">
              <p class="eyebrow">{{ copy.currentFocus }}</p>
              <h2 class="display-title !text-[clamp(2.6rem,4vw,4.8rem)]">{{ activeGame?.name || copy.currentFocus }}</h2>
              <p class="section-copy max-w-xl">{{ copy.focusCopy }}</p>
            </div>

            <form class="home-search-shell !mt-0" @submit.prevent="submitQuickSearch">
              <input
                v-model="quickQuery"
                type="text"
                class="home-search-input"
                :placeholder="`${copy.searchPrefix}${activeGame?.name || ''}${copy.searchSuffix}`"
              />
              <button type="submit" class="btn-ghost !px-4 !py-2.5">{{ copy.searchButton }}</button>
            </form>

            <div class="grid gap-4 sm:grid-cols-3">
              <article class="stat-card">
                <p class="text-xs uppercase tracking-[0.24em] text-slate-500">{{ copy.platform }}</p>
                <p class="mt-3 text-2xl font-semibold text-white">{{ getGamePlatformLabel(activeGame?.platform) }}</p>
              </article>
              <article class="stat-card">
                <p class="text-xs uppercase tracking-[0.24em] text-slate-500">{{ copy.modes }}</p>
                <p class="mt-3 text-2xl font-semibold text-white">{{ serviceTypes.length || '--' }}</p>
              </article>
              <article class="stat-card">
                <p class="text-xs uppercase tracking-[0.24em] text-slate-500">{{ copy.visibleServices }}</p>
                <template v-if="focusServiceCount > 0">
                  <p class="mt-3 text-2xl font-semibold text-white">{{ focusServiceCount }}</p>
                </template>
                <template v-else>
                  <p class="mt-3 text-2xl font-semibold text-white">{{ copy.noServiceShort }}</p>
                  <p class="mt-2 text-xs leading-6 text-slate-400">{{ copy.noServiceComing }}</p>
                </template>
              </article>
            </div>
          </div>
        </article>

        <div class="space-y-4">
          <button
            v-for="service in selectedGameServices"
            :key="service.id"
            type="button"
            class="home-service-card"
            @click="openService(service.id)"
          >
            <div class="flex items-start justify-between gap-4">
              <div>
                <p class="text-xs uppercase tracking-[0.24em] text-slate-500">{{ service.service_type }}</p>
                <h3 class="mt-3 text-2xl font-semibold text-white">{{ service.title }}</h3>
                <p class="mt-3 text-sm leading-7 text-slate-300">
                  {{ service.description || copy.serviceFallback }}
                </p>
              </div>
              <span class="tag">{{ formatPrice(service.price_per_hour) }}</span>
            </div>

            <div class="mt-5 flex items-center justify-between gap-4 text-sm text-slate-400">
              <p>{{ getGamePlatformLabel(activeGame?.platform) }}</p>
              <p>{{ copy.dealsPrefix }}{{ service.order_count || 0 }}</p>
            </div>
          </button>

          <article v-if="!selectedGameServices.length" class="empty-panel !border-solid !p-8 !text-left">
            <h3 class="text-2xl font-semibold text-white">{{ copy.noSampleTitle }}</h3>
            <p class="mt-3 text-sm leading-7 text-slate-400">{{ copy.noSampleCopy }}</p>
            <button type="button" class="btn-primary mt-6" @click="openGameZone">
              {{ copy.enterPrefix }}{{ activeGame?.name || '' }}
            </button>
          </article>
        </div>
      </div>
    </section>
  </div>
</template>
