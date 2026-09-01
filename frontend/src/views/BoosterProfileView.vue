<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/utils/api'
import { formatDateTime } from '@/utils/display'

const props = defineProps({
  id: {
    type: [String, Number],
    required: true,
  },
})

const route = useRoute()
const router = useRouter()

const booster = ref(null)
const services = ref([])
const reviews = ref([])
const loading = ref(true)
const error = ref('')

const LEVEL_META = {
  master: { label: '\u5927\u5e08\u4ee3\u7ec3', color: 'text-price', bg: 'bg-price-soft', icon: '\u{1f3c6}' },
  diamond: { label: '\u94bb\u77f3\u4ee3\u7ec3', color: 'text-info', bg: 'bg-info-soft', icon: '\u{1f48e}' },
  gold: { label: '\u9ec4\u91d1\u4ee3\u7ec3', color: 'text-warning', bg: 'bg-warning-soft', icon: '\u{1f31f}' },
  silver: { label: '\u767d\u94f6\u4ee3\u7ec3', color: 'text-ink-2', bg: 'bg-surface-3', icon: '\u{1f539}' },
  bronze: { label: '\u9752\u94dc\u4ee3\u7ec3', color: 'text-ink-2', bg: 'bg-surface-3', icon: '\u{1f7e0}' },
}

const levelMeta = computed(() => LEVEL_META[booster.value?.credit_level] || LEVEL_META.bronze)

const statsCards = computed(() => {
  if (!booster.value) return []
  return [
    { label: '\u4fe1\u8a89\u5206', value: booster.value.credit_score, icon: '\u2b50' },
    { label: '\u5b8c\u6210\u8ba2\u5355', value: booster.value.total_completed, icon: '\u{1f4cb}' },
    { label: '\u5b8c\u6210\u7387', value: `${booster.value.completion_rate}%`, icon: '\u2705' },
    { label: '\u5e73\u5747\u8bc4\u5206', value: booster.value.avg_rating > 0 ? booster.value.avg_rating.toFixed(1) : '-', icon: '\u{1f31f}' },
    { label: '\u5e73\u5747\u54cd\u5e94', value: booster.value.avg_response_minutes > 0 ? `${booster.value.avg_response_minutes}\u5206\u949f` : '-', icon: '\u26a1' },
    { label: '\u4e89\u8bae\u6b21\u6570', value: booster.value.total_disputed, icon: '\u26a0\ufe0f' },
  ]
})

function starsDisplay(rating) {
  return '\u2605'.repeat(rating) + '\u2606'.repeat(5 - rating)
}

async function fetchProfile() {
  loading.value = true
  error.value = ''
  try {
    const [profileResp, servicesResp, reviewsResp] = await Promise.all([
      api.get(`/users/${props.id}/profile`),
      api.get(`/users/${props.id}/services`),
      api.get(`/users/${props.id}/reviews`),
    ])
    booster.value = profileResp.data
    services.value = servicesResp.data || []
    reviews.value = reviewsResp.data?.items || []
  } catch (err) {
    error.value = err.message || '\u52a0\u8f7d\u5931\u8d25'
  } finally {
    loading.value = false
  }
}

onMounted(fetchProfile)
</script>

<template>
  <div class="page-shell space-y-6">
    <button class="btn-ghost self-start !px-0 text-sm" @click="router.back()">返回</button>

    <div v-if="loading" class="space-y-6" aria-busy="true">
      <div class="skeleton h-44 !rounded-panel"></div>
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <div v-for="n in 6" :key="`bp-skeleton-${n}`" class="skeleton h-20 !rounded-tile"></div>
      </div>
    </div>

    <div v-else-if="error" class="message-error">{{ error }}</div>

    <template v-else-if="booster">
      <!-- Hero section -->
      <section class="hero-panel p-6 sm:p-8 lg:p-10">
        <div class="relative z-10 flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
          <div class="flex items-center gap-5">
            <div class="relative">
              <div class="relative flex h-20 w-20 items-center justify-center rounded-[22px] border border-line-1 bg-primary-soft text-2xl font-semibold text-primary">
                {{ booster.username.charAt(0) }}
              </div>
            </div>
            <div class="space-y-2">
              <h1 class="section-title">{{ booster.username }}</h1>
              <div class="flex flex-wrap items-center gap-3">
                <span :class="['tag', levelMeta.bg, levelMeta.color]">
                  {{ levelMeta.label }}
                </span>
                <span class="text-sm text-ink-2">信誉分 {{ booster.credit_score }}</span>
                <span v-if="booster.avg_rating > 0" class="text-sm text-warning">
                  {{ starsDisplay(Math.round(booster.avg_rating)) }} {{ booster.avg_rating.toFixed(1) }}
                </span>
              </div>
              <div v-if="booster.badge_tags?.length" class="flex flex-wrap gap-2 pt-1">
                <span
                  v-for="tag in booster.badge_tags"
                  :key="tag"
                  class="rounded-full bg-primary-soft px-3 py-1 text-xs text-primary"
                >
                  {{ tag }}
                </span>
              </div>
            </div>
          </div>

          <div class="text-sm text-ink-2">
            <p v-if="booster.bio" class="max-w-md leading-relaxed">{{ booster.bio }}</p>
            <p class="mt-2">注册于 {{ formatDateTime(booster.created_at) }}</p>
          </div>
        </div>
      </section>

      <!-- Stats grid -->
      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <article
          v-for="card in statsCards"
          :key="card.label"
          class="stat-card flex items-center gap-4"
        >
          <div class="flex h-11 w-11 items-center justify-center rounded-tile border border-line-1 bg-primary-soft text-lg">
            {{ card.icon }}
          </div>
          <div>
            <p class="text-xs uppercase tracking-[0.18em] text-ink-3">{{ card.label }}</p>
            <p class="mt-1 text-lg font-semibold text-ink-1">{{ card.value }}</p>
          </div>
        </article>
      </div>

      <!-- Services -->
      <section class="surface-card p-6 sm:p-8">
        <h2 class="text-2xl font-semibold text-ink-1">服务列表</h2>
        <div v-if="services.length === 0" class="empty-state mt-4 !py-10">
          <div class="empty-state__icon !h-11 !w-11 !text-lg" aria-hidden="true">🧩</div>
          <p class="empty-state__copy">这位代练还没有发布服务。</p>
        </div>
        <div v-else class="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <article
            v-for="svc in services"
            :key="svc.id"
            class="stat-card cursor-pointer transition-all duration-base hover:-translate-y-0.5 hover:border-line-2"
            @click="router.push({ name: 'service-detail', params: { id: svc.id } })"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="tag">{{ svc.game?.name || '未知游戏' }}</span>
              <span class="text-xs text-ink-2">{{ svc.service_type }}</span>
            </div>
            <h3 class="mt-3 text-sm font-medium text-ink-1 line-clamp-2">{{ svc.title }}</h3>
            <div class="mt-3 flex items-center justify-between text-sm">
              <span class="font-semibold text-price">&yen;{{ svc.price_per_hour }}/h</span>
              <span class="text-ink-2">{{ svc.order_count }} 单</span>
            </div>
          </article>
        </div>
      </section>

      <!-- Reviews -->
      <section class="surface-card p-6 sm:p-8">
        <h2 class="text-2xl font-semibold text-ink-1">用户评价</h2>
        <div v-if="reviews.length === 0" class="empty-state mt-4 !py-10">
          <div class="empty-state__icon !h-11 !w-11 !text-lg" aria-hidden="true">💬</div>
          <p class="empty-state__copy">还没有评价，完成订单后会出现这里。</p>
        </div>
        <div v-else class="mt-6 space-y-4">
          <article
            v-for="review in reviews"
            :key="review.id"
            class="info-tile !p-5"
          >
            <div class="flex items-center justify-between gap-3">
              <span class="text-sm text-ink-2">{{ review.reviewer?.username || '匿名用户' }}</span>
              <span class="text-warning text-sm">{{ starsDisplay(review.rating) }}</span>
            </div>
            <p v-if="review.content" class="mt-2 text-sm text-ink-2 leading-relaxed">{{ review.content }}</p>
            <p class="mt-2 text-xs text-ink-3">{{ formatDateTime(review.created_at) }}</p>
          </article>
        </div>
      </section>
    </template>

    <section v-else class="empty-state">
      <div class="empty-state__icon" aria-hidden="true">🥷</div>
      <h2 class="empty-state__title">代练不存在</h2>
      <p class="empty-state__copy">换个 ID 试试，或回到订单大厅找其他代练。</p>
    </section>
  </div>
</template>
