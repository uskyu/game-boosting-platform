<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useGamesStore } from '@/stores/games'
import { useServicesStore } from '@/stores/services'
import { formatPrice } from '@/utils/display'
import {
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

const router = useRouter()
const authStore = useAuthStore()
const gamesStore = useGamesStore()
const servicesStore = useServicesStore()

const submitMessage = ref('')
const submitError = ref('')
const orderForm = ref({
  description_raw: '',
  estimated_hours: '1',
  current_rank: '',
  target_rank: '',
  server: '',
  price: '',
  notes: '',
  game_account: '',
  game_password: '',
})

const loading = computed(() => servicesStore.loading)
const submitting = computed(() => servicesStore.submitting)
const service = computed(() => servicesStore.currentService)
const game = computed(() => {
  if (!service.value?.game_id) {
    return null
  }
  return gamesStore.getGameById(service.value.game_id) || null
})
const isOwner = computed(() => service.value && authStore.user?.id === service.value.booster_id)
const canOrder = computed(() => authStore.isAuthenticated && !authStore.isBooster && !authStore.isAdmin && !isOwner.value)
const serviceTypes = computed(() => getGameServiceTypes(game.value))
const editing = ref(false)
const editForm = ref({ title: '', description: '', price_per_hour: '', service_type: '' })

function startEdit() {
  editForm.value = {
    title: service.value.title || '',
    description: service.value.description || '',
    price_per_hour: String(service.value.price_per_hour || ''),
    service_type: service.value.service_type || '',
  }
  editing.value = true
}

async function handleSaveEdit() {
  submitError.value = ''
  submitMessage.value = ''

  const payload = {
    ...editForm.value,
    price_per_hour: Number(editForm.value.price_per_hour),
  }

  const result = await servicesStore.updateService(props.id, payload)
  if (!result.success) {
    submitError.value = result.error
    return
  }

  editing.value = false
  submitMessage.value = '服务信息已更新'
}

async function handleOrder() {
  submitError.value = ''
  submitMessage.value = ''

  const payload = {
    ...orderForm.value,
    estimated_hours: Number(orderForm.value.estimated_hours || 1),
    price: orderForm.value.price ? Number(orderForm.value.price) : undefined,
  }

  const result = await servicesStore.orderFromService(props.id, payload)
  if (!result.success) {
    submitError.value = result.error
    return
  }

  submitMessage.value = '订单已创建，正在跳转到订单详情。'
  window.setTimeout(() => {
    router.push({ name: 'order-detail', params: { id: result.data.id } })
  }, 900)
}

onMounted(async () => {
  await gamesStore.ensureCatalog()
  const result = await servicesStore.fetchService(props.id)
  if (result.success && result.data?.game_id && !gamesStore.getGameById(result.data.game_id)) {
    await gamesStore.fetchGame(result.data.game_id)
  }
})
</script>

<template>
  <div class="page-shell space-y-8">
    <button class="btn-ghost self-start !px-0 text-sm" @click="router.back()">返回服务列表</button>

    <div v-if="loading" class="space-y-8" aria-busy="true">
      <div class="skeleton h-56 !rounded-panel"></div>
      <div class="grid gap-6 xl:grid-cols-[0.98fr_1.02fr]">
        <div class="skeleton h-96 !rounded-card"></div>
        <div class="skeleton h-96 !rounded-card"></div>
      </div>
    </div>

    <template v-else-if="service">
      <section class="hero-panel scanline-overlay p-6 sm:p-8 lg:p-10" :style="buildGameSurfaceStyle(game)">
        <div class="absolute inset-0 bg-gradient-to-r from-slate-950/92 via-slate-950/82 to-slate-950/66"></div>

        <div class="relative z-10 grid gap-8 lg:grid-cols-[1.08fr_0.92fr] lg:items-end">
          <div class="space-y-4">
            <div class="flex flex-wrap items-center gap-3">
              <span class="eyebrow">{{ game?.name || `游戏 #${service.game_id}` }}</span>
              <span class="tag">{{ service.service_type }}</span>
              <span class="tag">{{ getGamePlatformLabel(game?.platform) }}</span>
            </div>
            <h1 class="section-title neon-text !text-4xl sm:!text-5xl">{{ service.title }}</h1>
            <p class="section-copy max-w-3xl">
              {{ service.description || '这张卡片可以直接下单。' }}
            </p>

            <div class="flex flex-wrap gap-2">
              <span
                v-for="tag in (service.tags || serviceTypes).slice(0, 5)"
                :key="tag"
                class="tag"
              >
                {{ tag }}
              </span>
            </div>
          </div>

          <div class="grid gap-4 sm:grid-cols-3">
            <article class="stat-card cyber-corner">
              <p class="text-sm text-slate-400">每小时价格</p>
              <p class="mt-2 text-3xl font-semibold text-accent-300">{{ formatPrice(service.price_per_hour) }}</p>
            </article>
            <article class="stat-card cyber-corner">
              <p class="text-sm text-slate-400">已完成订单</p>
              <p class="mt-2 text-3xl font-semibold text-white">{{ service.order_count }}</p>
            </article>
            <article class="stat-card cyber-corner">
              <p class="text-sm text-slate-400">当前状态</p>
              <p class="mt-2 text-3xl font-semibold text-white">{{ service.is_available ? '可下单' : '已下架' }}</p>
            </article>
          </div>
        </div>
      </section>

      <div class="grid gap-6 xl:grid-cols-[0.98fr_1.02fr]">
        <section class="surface-card p-6 sm:p-8">
          <h2 class="text-2xl font-semibold text-white">服务信息</h2>

          <div class="mt-6 grid gap-4 sm:grid-cols-2">
            <div class="info-tile">
              <p class="info-tile__label">服务编号</p>
              <p class="info-tile__value">#{{ service.id }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">所属游戏</p>
              <p class="info-tile__value">{{ game?.name || `游戏 #${service.game_id}` }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">发布者</p>
              <p class="info-tile__value">代练 #{{ service.booster_id }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">平台</p>
              <p class="info-tile__value">{{ getGamePlatformLabel(game?.platform) }}</p>
            </div>
          </div>

          <div class="info-tile mt-6">
            <p class="text-sm font-medium text-primary-100">服务描述</p>
            <p class="mt-3 text-sm leading-7 text-slate-300">
              {{ service.description || '服务发布者暂时没有补充更多说明。' }}
            </p>
          </div>

          <div class="info-tile mt-6">
            <p class="text-sm font-medium text-primary-100">标签</p>
            <div class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="tag in (service.tags || serviceTypes)"
                :key="tag"
                class="tag"
              >
                {{ tag }}
              </span>
            </div>
          </div>
        </section>

        <aside class="surface-card p-6 sm:p-8">
          <template v-if="!authStore.isAuthenticated">
            <h2 class="text-2xl font-semibold text-white">先登录再下单</h2>
            <p class="mt-3 text-sm leading-7 text-slate-400">
              服务详情可以先看，但发起订单前需要先登录账号。
            </p>
            <router-link class="btn-primary mt-6" :to="{ name: 'login', query: { redirect: `/services/${service.id}` } }">
              去登录
            </router-link>
          </template>

          <template v-else-if="isOwner">
            <h2 class="text-2xl font-semibold text-white">管理你的服务</h2>

            <div v-if="submitError" class="message-error mt-4">{{ submitError }}</div>
            <div v-if="submitMessage" class="message-success mt-4">{{ submitMessage }}</div>

            <template v-if="editing">
              <div class="mt-6 grid gap-4">
                <div>
                  <label class="label">服务标题</label>
                  <input v-model="editForm.title" type="text" class="input" />
                </div>
                <div>
                  <label class="label">服务类型</label>
                  <select v-model="editForm.service_type" class="input">
                    <option v-for="st in serviceTypes" :key="st" :value="st">{{ st }}</option>
                  </select>
                </div>
                <div>
                  <label class="label">每小时价格</label>
                  <input v-model="editForm.price_per_hour" type="number" min="1" step="0.01" class="input" />
                </div>
                <div>
                  <label class="label">描述</label>
                  <textarea v-model="editForm.description" rows="4" class="input resize-none"></textarea>
                </div>
              </div>
              <div class="mt-4 flex gap-3">
                <button class="btn-primary !px-4 !py-2" :disabled="submitting" @click="handleSaveEdit">
                  {{ submitting ? '保存中...' : '保存修改' }}
                </button>
                <button class="btn-ghost !px-4 !py-2" @click="editing = false">取消</button>
              </div>
            </template>

            <template v-else>
              <p class="mt-3 text-sm leading-7 text-slate-400">这张服务卡片属于你，可以编辑信息。</p>
              <div class="mt-4 flex gap-3">
                <button class="btn-primary !px-4 !py-2" @click="startEdit">编辑服务</button>
                <router-link class="btn-secondary !px-4 !py-2" :to="{ name: 'services' }">返回列表</router-link>
              </div>
            </template>
          </template>

          <template v-else-if="!canOrder">
            <h2 class="text-2xl font-semibold text-white">当前无需下单</h2>
            <p class="mt-3 text-sm leading-7 text-slate-400">
              当前账号身份不适合直接从服务卡片下单。
            </p>
            <router-link class="btn-secondary mt-6" :to="{ name: 'services' }">
              返回服务列表
            </router-link>
          </template>

          <template v-else>
            <h2 class="text-2xl font-semibold text-white">从服务卡片直接下单</h2>
            <p class="mt-3 text-sm leading-7 text-slate-400">
              下单后会直接开聊天。
            </p>

            <div v-if="submitError" class="message-error mt-4">{{ submitError }}</div>
            <div v-if="submitMessage" class="message-success mt-4">{{ submitMessage }}</div>

            <div class="mt-6 grid gap-4">
              <div>
                <label class="label" for="service-order-desc">需求描述</label>
                <textarea id="service-order-desc" v-model="orderForm.description_raw" rows="4" class="input resize-none" placeholder="描述你这次的目标、时间要求和偏好。"></textarea>
              </div>

              <div class="grid gap-4 sm:grid-cols-2">
                <div>
                  <label class="label" for="service-order-hours">预估时长</label>
                  <input id="service-order-hours" v-model="orderForm.estimated_hours" type="number" min="1" step="0.5" class="input" />
                </div>
                <div>
                  <label class="label" for="service-order-price">确认价格（可选）</label>
                  <input id="service-order-price" v-model="orderForm.price" type="number" min="1" step="0.01" class="input" placeholder="留空则按时长计算" />
                </div>
              </div>

              <div class="grid gap-4 sm:grid-cols-2">
                <div>
                  <label class="label" for="service-order-current">当前段位</label>
                  <input id="service-order-current" v-model="orderForm.current_rank" type="text" class="input" placeholder="例如：星耀三" />
                </div>
                <div>
                  <label class="label" for="service-order-target">目标段位</label>
                  <input id="service-order-target" v-model="orderForm.target_rank" type="text" class="input" placeholder="例如：王者" />
                </div>
              </div>

              <div>
                <label class="label" for="service-order-server">区服</label>
                <input id="service-order-server" v-model="orderForm.server" type="text" class="input" placeholder="例如：微信区 / 艾欧尼亚" />
              </div>

              <div>
                <label class="label" for="service-order-notes">补充说明</label>
                <textarea id="service-order-notes" v-model="orderForm.notes" rows="3" class="input resize-none" placeholder="例如：晚上 8 点后开打，希望全程语音沟通。"></textarea>
              </div>
            </div>

            <button class="btn-primary mt-6" :disabled="submitting" @click="handleOrder">
              {{ submitting ? '正在创建订单...' : '确认从服务卡片下单' }}
            </button>
          </template>
        </aside>
      </div>
    </template>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🃏</div>
      <h2 class="empty-state__title">服务不存在或暂时不可见</h2>
      <p class="empty-state__copy">这张服务卡片可能已被下架，或者你暂时没有权限查看。</p>
    </section>
  </div>
</template>
