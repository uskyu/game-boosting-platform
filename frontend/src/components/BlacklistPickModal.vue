<script setup>
import { computed, ref, watch } from 'vue'

import { BLACKLIST_BLOCK_NOTICE, useBlacklistStore } from '@/stores/blacklist'
import { formatDateTime } from '@/utils/display'
import { getClaimStatusMeta } from '@/utils/order'
import api from '@/utils/api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  orderId: { type: [String, Number], default: null },
  orderTitle: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'blocked'])

const blacklistStore = useBlacklistStore()

const claims = ref([])
const loading = ref(false)
const loadError = ref('')
const confirmTargetId = ref(null)
const submittingId = ref(null)
const blockError = ref('')
const blockSuccess = ref('')
let loadSeq = 0

const blockedCount = computed(
  () => claims.value.filter((claim) => blacklistStore.isBlocked(claim.booster_id)).length,
)

function claimBoosterName(claim) {
  return claim?.booster_nickname || (claim?.booster_id != null ? `用户 #${claim.booster_id}` : '打手')
}

function claimStatusMeta(claim) {
  return getClaimStatusMeta(claim?.status)
}

// 快捷拉黑的数据源只有本单的报名名单（GET /orders/{id}/claims），
// 不请求任何全站用户列表/模糊搜索。
async function loadClaims() {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const response = await api.get(`/orders/${props.orderId}/claims`)
    if (seq !== loadSeq) return
    claims.value = Array.isArray(response.data?.items) ? response.data.items : []
  } catch (err) {
    if (seq !== loadSeq) return
    claims.value = []
    loadError.value = err?.message || '接单人加载失败，请稍后重试'
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// 拉取当前发单人自己的黑名单（仅用于标记「已拉黑」），分页拉全但最多 5 页
// （后端单发单人与接口都按最多 500 条设计）。这里拉的是自己的黑名单，
// 不是全站用户列表。
async function loadKnownBlocked() {
  const first = await blacklistStore.fetchMine(1, 100)
  if (!first.success) return
  const pages = Math.min(Math.max(1, Number(blacklistStore.pagination.pages) || 1), 5)
  for (let page = 2; page <= pages; page += 1) {
    // 弹窗已关闭就不再继续翻页（该请求只用于「已拉黑」标记）
    if (!props.modelValue) return
    await blacklistStore.fetchMine(page, 100)
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    confirmTargetId.value = null
    submittingId.value = null
    blockError.value = ''
    blockSuccess.value = ''
    claims.value = []
    if (props.orderId != null) {
      loadClaims()
      loadKnownBlocked()
    }
  },
  { immediate: true },
)

function close() {
  if (submittingId.value) return
  emit('update:modelValue', false)
}

async function submitBlock(claim) {
  if (submittingId.value != null || !claim?.booster_id) return
  const userId = Number(claim.booster_id)
  submittingId.value = userId
  blockError.value = ''
  blockSuccess.value = ''
  const result = await blacklistStore.add(userId)
  submittingId.value = null
  if (result.success) {
    confirmTargetId.value = null
    blockSuccess.value = `已拉黑 ${claimBoosterName(claim)}`
    emit('blocked', { userId, claim })
  } else {
    blockError.value = result.error || '拉黑失败，请稍后重试'
  }
}
</script>

<template>
  <teleport to="body">
    <div v-if="modelValue" class="modal-scrim" @click.self="close">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="拉黑接单人">
        <h3 class="text-lg font-semibold text-ink-1">拉黑接单人</h3>
        <p class="mt-2 text-sm leading-6 text-ink-2">
          从「{{ orderTitle || '该订单' }}」的接单人中选择要拉黑的人。
        </p>
        <p class="message-info mt-3 text-xs leading-6">{{ BLACKLIST_BLOCK_NOTICE }}</p>

        <div v-if="loading" class="mt-4 space-y-2" aria-busy="true">
          <div v-for="n in 3" :key="`blacklist-claim-skeleton-${n}`" class="skeleton-line h-12 w-full"></div>
        </div>
        <p v-else-if="loadError" class="message-error mt-4 text-sm">
          {{ loadError }}
          <button type="button" class="btn-ghost !min-h-[32px] !px-3 !py-1 text-xs" @click="loadClaims">重新加载</button>
        </p>
        <div v-else-if="claims.length" class="mt-4 max-h-[60vh] space-y-2 overflow-y-auto">
          <div
            v-for="claim in claims"
            :key="claim.id || claim.booster_id"
            class="info-tile"
          >
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="min-w-0">
                <p class="truncate text-sm font-medium text-ink-1">
                  {{ claimBoosterName(claim) }}
                  <span class="ml-1 text-xs text-ink-3">#{{ claim.booster_id }}</span>
                </p>
                <p class="mt-1 text-xs text-ink-3">接单时间：{{ formatDateTime(claim.created_at) }}</p>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <span :class="claimStatusMeta(claim).tagClass">{{ claimStatusMeta(claim).label }}</span>
                <span v-if="blacklistStore.isBlocked(claim.booster_id)" class="tag !bg-surface-3 !text-ink-2">已拉黑</span>
                <button
                  v-else
                  type="button"
                  class="btn-secondary !min-h-[32px] !px-3 !py-1 text-xs"
                  @click="confirmTargetId = claim.booster_id"
                >
                  拉黑
                </button>
              </div>
            </div>

            <!-- 二次确认就地展开：说明拉黑只影响后续接单，不追溯已接订单 -->
            <div
              v-if="confirmTargetId === claim.booster_id"
              class="mt-3 rounded-tile border border-warning/40 bg-warning-soft p-3 text-xs leading-6 text-ink-2"
            >
              <p>{{ BLACKLIST_BLOCK_NOTICE }}</p>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="btn-danger !min-h-[32px] !px-3 !py-1 text-xs"
                  :disabled="submittingId === Number(claim.booster_id)"
                  @click="submitBlock(claim)"
                >
                  {{ submittingId === Number(claim.booster_id) ? '提交中…' : '确认拉黑' }}
                </button>
                <button
                  type="button"
                  class="btn-ghost !min-h-[32px] !px-3 !py-1 text-xs"
                  :disabled="submittingId === Number(claim.booster_id)"
                  @click="confirmTargetId = null"
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        </div>
        <p v-else class="mt-4 text-sm text-ink-3">该订单暂无接单人记录。</p>

        <p v-if="blockSuccess" class="message-success mt-3 text-sm">{{ blockSuccess }}</p>
        <p v-if="blockError" class="message-error mt-3 text-sm">{{ blockError }}</p>
        <p v-if="blockedCount" class="mt-3 text-xs text-ink-3">
          本单已有 {{ blockedCount }} 人被拉黑，
          <router-link :to="{ name: 'blacklist' }" class="text-primary underline-offset-4 hover:underline">去黑名单页解除</router-link>
        </p>

        <div class="mt-6 flex gap-3">
          <button type="button" class="btn-secondary flex-1" :disabled="submittingId != null" @click="close">关闭</button>
        </div>
      </div>
    </div>
  </teleport>
</template>
