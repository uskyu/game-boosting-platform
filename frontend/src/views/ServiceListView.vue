<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useGamesStore } from '@/stores/games'
import { useServicesStore } from '@/stores/services'
import { formatPrice } from '@/utils/display'
import { getServiceTypeCTA } from '@/utils/humanCopy'
import {
  buildAccentStyle,
  buildGameSurfaceStyle,
  getGamePlatformLabel,
  getGameServiceTypes,
} from '@/utils/gameCatalog'

const router = useRouter()
const authStore = useAuthStore()
const gamesStore = useGamesStore()
const servicesStore = useServicesStore()

const priceMin = ref('')
const priceMax = ref('')
const selectedGameId = ref('')
const selectedServiceType = ref('')
const publishTags = ref('')
const publishForm = ref({
  game_id: '',
  title: '',
  description: '',
  service_type: '',
  price_per_hour: '',
})
const publishMessage = ref('')
const publishError = ref('')

const loading = computed(() => servicesStore.loading)
const submitting = computed(() => servicesStore.submitting)
const services = computed(() => servicesStore.services)
const myServices = computed(() => servicesStore.myServices)
const pagination = computed(() => servicesStore.pagination)
const isBooster = computed(() => authStore.isBooster)

const selectedFilterGame = computed(() => {
  return selectedGameId.value ? gamesStore.getGameById(Number(selectedGameId.value)) : null
})

const publishGame = computed(() => {
  return publishForm.value.game_id ? gamesStore.getGameById(Number(publishForm.value.game_id)) : null
})

const availableFilterServiceTypes = computed(() => {
  if (!selectedFilterGame.value) {
    return Array.from(
      new Set(
        gamesStore.catalogGames.flatMap((game) => getGameServiceTypes(game))
      )
    )
  }
  return getGameServiceTypes(selectedFilterGame.value)
})

const publishServiceTypes = computed(() => {
  if (!publishGame.value) {
    return []
  }
  return getGameServiceTypes(publishGame.value)
})

function getGame(service) {
  return gamesStore.getGameById(service.game_id)
}

async function loadServices(page = 1) {
  await servicesStore.fetchServices({
    page,
    page_size: 12,
    game_id: selectedGameId.value || '',
    service_type: selectedServiceType.value || '',
    price_min: priceMin.value || '',
    price_max: priceMax.value || '',
  })
}

function resetFilters() {
  selectedGameId.value = ''
  selectedServiceType.value = ''
  priceMin.value = ''
  priceMax.value = ''
  loadServices(1)
}

function openService(serviceId) {
  router.push({ name: 'service-detail', params: { id: serviceId } })
}

async function handleCreateService() {
  publishError.value = ''
  publishMessage.value = ''

  const payload = {
    ...publishForm.value,
    game_id: Number(publishForm.value.game_id),
    price_per_hour: Number(publishForm.value.price_per_hour),
    tags: publishTags.value
      .split(/[，,]/)
      .map((item) => item.trim())
      .filter(Boolean),
  }

  const result = await servicesStore.createService(payload)
  if (!result.success) {
    publishError.value = result.error
    return
  }

  publishForm.value = {
    game_id: '',
    title: '',
    description: '',
    service_type: '',
    price_per_hour: '',
  }
  publishTags.value = ''
  publishMessage.value = '服务卡片已发布。'
  await loadServices(1)
  await servicesStore.fetchMyServices({ page: 1, pageSize: 12 })
}

async function handleDeleteService(serviceId) {
  const confirmed = window.confirm('确认下架这张服务卡片吗？')
  if (!confirmed) {
    return
  }

  await servicesStore.deleteService(serviceId)
  await loadServices(pagination.value.page)
  await servicesStore.fetchMyServices({ page: 1, pageSize: 12 })
}

function syncPublishServiceType() {
  if (!publishServiceTypes.value.includes(publishForm.value.service_type)) {
    publishForm.value.service_type = publishServiceTypes.value[0] || ''
  }
}

onMounted(async () => {
  await gamesStore.ensureCatalog()
  await loadServices(1)

  if (isBooster.value) {
    await servicesStore.fetchMyServices({ page: 1, pageSize: 12 })
  }
})
</script>

<template>
  <div class="page-shell space-y-8">
    <section class="hero-panel scanline-overlay p-6 sm:p-8 lg:p-10">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div class="space-y-4">
          <p class="eyebrow">陪玩服务</p>
          <h1 class="section-title neon-text !text-4xl sm:!text-5xl">
            找陪玩，也能上架自己的服务。
          </h1>
          <p class="section-copy max-w-3xl">
            按游戏、类型、价格筛。点进卡片，直接下单。
          </p>
        </div>

        <div class="grid gap-4 sm:grid-cols-3">
          <article class="stat-card cyber-corner">
            <p class="text-3xl font-semibold text-white">{{ pagination.total }}</p>
            <p class="mt-2 text-sm text-slate-300">现在可看服务</p>
          </article>
          <article class="stat-card cyber-corner">
            <p class="text-3xl font-semibold text-white">{{ gamesStore.catalogGames.length }}</p>
            <p class="mt-2 text-sm text-slate-300">覆盖游戏</p>
          </article>
          <article class="stat-card cyber-corner">
            <p class="text-3xl font-semibold text-white">{{ isBooster ? myServices.length : '实时' }}</p>
            <p class="mt-2 text-sm text-slate-300">{{ isBooster ? '我发布的' : '实时更新' }}</p>
          </article>
        </div>
      </div>
    </section>

    <section class="surface-card p-5 sm:p-6">
      <div class="grid gap-4 lg:grid-cols-[1.3fr_1fr_1fr_auto] lg:items-end">
        <div>
          <label class="label" for="service-filter-game">打哪个</label>
          <select id="service-filter-game" v-model="selectedGameId" class="input" @change="loadServices(1)">
            <option value="">全部游戏</option>
            <option v-for="game in gamesStore.catalogGames" :key="game.id" :value="game.id">{{ game.name }}</option>
          </select>
        </div>

        <div>
          <label class="label" for="service-filter-type">我想要</label>
          <select id="service-filter-type" v-model="selectedServiceType" class="input" @change="loadServices(1)">
            <option value="">全部类型</option>
            <option
              v-for="serviceType in availableFilterServiceTypes"
              :key="serviceType"
              :value="serviceType"
            >
              {{ serviceType }}
            </option>
          </select>
        </div>

        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="label" for="service-price-min">最低价</label>
            <input id="service-price-min" v-model="priceMin" type="number" min="0" class="input" placeholder="0" />
          </div>
          <div>
            <label class="label" for="service-price-max">最高价</label>
            <input id="service-price-max" v-model="priceMax" type="number" min="0" class="input" placeholder="999" />
          </div>
        </div>

        <div class="flex gap-3 lg:self-end">
          <button class="btn-secondary !px-4 !py-3" @click="loadServices(1)">筛选</button>
          <button class="btn-ghost !px-4 !py-3" @click="resetFilters">重置</button>
        </div>
      </div>
    </section>

    <section v-if="isBooster" class="surface-card p-6 sm:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-start">
        <div class="lg:w-80">
          <p class="eyebrow">发布服务</p>
          <h2 class="section-title mt-4">把你会打的内容挂出来</h2>
          <p class="section-copy mt-3">写清楚你擅长什么，老板自己会找来。</p>
        </div>

        <div class="flex-1 space-y-4">
          <div v-if="publishError" class="message-error">{{ publishError }}</div>
          <div v-if="publishMessage" class="message-success">{{ publishMessage }}</div>

          <div class="grid gap-4 md:grid-cols-2">
            <div>
              <label class="label" for="publish-game">游戏</label>
              <select id="publish-game" v-model="publishForm.game_id" class="input" @change="syncPublishServiceType">
                <option value="">选择一个游戏</option>
                <option v-for="game in gamesStore.catalogGames" :key="game.id" :value="game.id">{{ game.name }}</option>
              </select>
            </div>

            <div>
              <label class="label" for="publish-type">服务类型</label>
              <select id="publish-type" v-model="publishForm.service_type" class="input">
                <option value="">选择服务类型</option>
                <option
                  v-for="serviceType in publishServiceTypes"
                  :key="serviceType"
                  :value="serviceType"
                >
                  {{ serviceType }}
                </option>
              </select>
            </div>

            <div class="md:col-span-2">
              <label class="label" for="publish-title">服务标题</label>
              <input id="publish-title" v-model="publishForm.title" type="text" class="input" placeholder="例如：王者荣耀百星打野陪玩" />
            </div>

            <div>
              <label class="label" for="publish-price">每小时价格</label>
              <input id="publish-price" v-model="publishForm.price_per_hour" type="number" min="1" step="0.01" class="input" placeholder="例如：88" />
            </div>

            <div>
              <label class="label" for="publish-tags">标签</label>
              <input id="publish-tags" v-model="publishTags" type="text" class="input" placeholder="国服百强, 晚间在线, 语音指挥" />
            </div>

            <div class="md:col-span-2">
              <label class="label" for="publish-description">描述</label>
              <textarea id="publish-description" v-model="publishForm.description" rows="4" class="input resize-none" placeholder="说明你的打法、擅长内容、可服务时间和沟通方式。"></textarea>
            </div>
          </div>

          <button class="btn-primary" :disabled="submitting" @click="handleCreateService">
            {{ submitting ? '发布中...' : '发布服务卡片' }}
          </button>
        </div>
      </div>
    </section>

    <div v-if="servicesStore.error" class="message-error">{{ servicesStore.error }}</div>

    <section v-if="loading" class="grid gap-5 xl:grid-cols-3" aria-busy="true">
      <div v-for="n in 6" :key="`svc-skeleton-${n}`" class="skeleton h-72 !rounded-card"></div>
    </section>

    <section v-else-if="services.length" class="grid gap-5 xl:grid-cols-3">
      <button
        v-for="service in services"
        :key="service.id"
        type="button"
        class="catalog-card cyber-corner text-left"
        @click="openService(service.id)"
      >
        <div class="cover-card p-5" :style="buildGameSurfaceStyle(getGame(service))">
          <div class="relative z-10 flex h-full flex-col justify-between">
            <div class="flex items-center justify-between gap-4">
              <span class="tag">{{ service.service_type }}</span>
              <div
                class="flex h-11 w-11 items-center justify-center rounded-tile border text-sm font-semibold text-white"
                :style="buildAccentStyle(getGame(service))"
              >
                服
              </div>
            </div>

            <div class="space-y-3">
              <p class="text-sm text-slate-300">{{ getGame(service)?.name || `游戏 #${service.game_id}` }}</p>
              <h2 class="text-2xl font-semibold text-white">{{ service.title }}</h2>
              <p class="text-sm leading-6 text-slate-300">{{ service.description || '这张服务卡片已经准备好接单。' }}</p>
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
            <p>成交 {{ service.order_count }} 单</p>
            <p class="mt-2 font-medium text-primary-300">{{ getServiceTypeCTA(service.service_type) }}</p>
          </div>
        </div>
      </button>
    </section>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🎮</div>
      <h2 class="empty-state__title">当前还没有匹配到服务</h2>
      <p class="empty-state__copy">可以切换筛选条件，或者稍后回来看看新上架的陪玩服务。</p>
    </section>

    <section v-if="isBooster && myServices.length" class="surface-card p-6 sm:p-8">
      <div class="flex items-end justify-between gap-4">
        <div>
          <p class="eyebrow">我的服务</p>
          <h2 class="section-title mt-4">你已经发布的服务卡片</h2>
        </div>
      </div>

      <div class="mt-6 grid gap-4 xl:grid-cols-2">
        <article
          v-for="service in myServices"
          :key="`my-${service.id}`"
          class="info-tile !p-5 transition-all duration-200 hover:!border-line-strong hover:bg-white/[0.055]"
        >
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="text-sm text-primary-100">{{ getGame(service)?.name || `游戏 #${service.game_id}` }}</p>
              <h3 class="mt-2 text-xl font-semibold text-white">{{ service.title }}</h3>
              <p class="mt-3 text-sm leading-6 text-slate-400">{{ service.description || '暂无补充说明。' }}</p>
            </div>
            <span :class="service.is_available ? 'badge-approved' : 'badge-cancelled'">
              {{ service.is_available ? '上架中' : '已下架' }}
            </span>
          </div>

          <div class="mt-5 flex items-center justify-between gap-4">
            <p class="text-xl font-semibold text-accent-300">{{ formatPrice(service.price_per_hour) }}</p>
            <div class="flex gap-2">
              <button class="btn-secondary !px-4 !py-2" @click="openService(service.id)">查看</button>
              <button
                v-if="service.is_available"
                class="btn-danger !px-4 !py-2"
                @click="handleDeleteService(service.id)"
              >
                下架
              </button>
            </div>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
