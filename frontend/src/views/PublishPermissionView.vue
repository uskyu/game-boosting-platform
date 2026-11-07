<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import {
  usePublishPermissionStore,
  getPublishPermissionStatusLabel,
  getPublishPermissionStatusTagClass,
} from '@/stores/publishPermission'
import { formatDateTime } from '@/utils/display'

const route = useRoute()
const authStore = useAuthStore()
const store = usePublishPermissionStore()

const reason = ref('')
const notice = ref({ type: '', text: '' })
const confirmWithdraw = ref(false)
const loaded = ref(false)

// 优先用 /publish-permission/mine 的结果；首次加载前先按 authStore 的默认值展示，
// 管理员始终视为已开通。
const canPublish = computed(() => (
  authStore.isAdmin
    ? true
    : loaded.value
      ? store.mine.can_publish === true
      : authStore.canPublish
))
const applyEnabled = computed(() => store.mine.apply_enabled !== false)
const latest = computed(() => store.mine.latest || null)
const isPending = computed(() => store.mine.has_pending === true || latest.value?.status === 'PENDING')
const statusLabel = computed(() => getPublishPermissionStatusLabel(latest.value?.status))
const statusTagClass = computed(() => getPublishPermissionStatusTagClass(latest.value?.status))
const canApply = computed(() => !isPending.value && applyEnabled.value)

// 路由守卫被发单权限拦截时会带上 redirect；只接受站内绝对路径：
// 必须以单个 / 开头且不能是 //（协议相对地址），其他值一律忽略，防开放重定向。
function isSafeRedirectTarget(value) {
  if (typeof value !== 'string') return false
  if (!value.startsWith('/') || value.startsWith('//')) return false
  // 部分浏览器会把反斜杠规范化成 /，同样可用于协议相对跳转
  return !value.includes('\\')
}

const redirectTarget = computed(() => {
  const value = route.query.redirect
  const path = Array.isArray(value) ? value[0] : value
  return isSafeRedirectTarget(path) ? path : null
})

function noticeClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  if (type === 'warning') return 'message-warning'
  return 'message-info'
}

async function loadMine() {
  const result = await store.fetchMine()
  // 拉取失败时继续按 authStore 的权限回退展示，避免把已开通用户误判成未开通
  if (result.success) loaded.value = true
  return result
}

async function refreshPermission() {
  notice.value = { type: '', text: '' }
  // 两个接口都成功才提示刷新成功；任一失败给出可重试反馈
  const [mineResult, userResult] = await Promise.all([loadMine(), authStore.fetchCurrentUser()])
  notice.value = mineResult?.success && userResult?.success
    ? { type: 'success', text: '权限状态已刷新' }
    : { type: 'error', text: '刷新失败，请稍后重试' }
}

async function submitApply() {
  notice.value = { type: '', text: '' }
  if (reason.value.length > 500) {
    notice.value = { type: 'error', text: '申请理由不能超过 500 字' }
    return
  }
  const result = await store.apply(reason.value)
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '提交申请失败' }
    return
  }
  reason.value = ''
  confirmWithdraw.value = false
  // 提交后刷新申请状态与用户权限，页面直接切到「待审核」
  await Promise.all([loadMine(), authStore.fetchCurrentUser()])
  notice.value = { type: 'success', text: '申请已提交，请等待管理员审核' }
}

async function withdrawLatest() {
  if (!latest.value?.id) return
  if (!confirmWithdraw.value) {
    confirmWithdraw.value = true
    return
  }
  const result = await store.withdraw(latest.value.id)
  confirmWithdraw.value = false
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '撤回失败' }
    return
  }
  await Promise.all([loadMine(), authStore.fetchCurrentUser()])
  notice.value = { type: 'success', text: '申请已撤回，可重新提交' }
}

onMounted(async () => {
  // 清空上一账号残留的申请状态，并立即置 loading 避免闪空
  store.reset()
  store.loading = true
  await Promise.all([authStore.fetchCurrentUser(), loadMine()])
})
</script>

<template>
  <div class="page-shell mx-auto max-w-3xl space-y-5">
    <section class="hero-panel p-6 sm:p-8">
      <p class="eyebrow">发单权限</p>
      <h1 class="section-title">发单权限</h1>
      <p class="section-copy max-w-2xl">
        发单权限决定你能否发布订单。管理员发单不受限制；普通用户可在此查看权限状态、申请开通或撤回申请。
      </p>
      <button
        type="button"
        class="btn-secondary mt-5 min-h-[44px] !px-5"
        :disabled="store.loading"
        @click="refreshPermission"
      >
        {{ store.loading ? '刷新中...' : '刷新权限' }}
      </button>
    </section>

    <div v-if="notice.text" :class="noticeClass(notice.type)">{{ notice.text }}</div>

    <!-- 已开通 -->
    <section v-if="canPublish" class="surface-card p-6 sm:p-8">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="text-xl font-semibold text-ink-1">发单权限已开通</h2>
        <span class="tag !bg-success-soft !text-success">已开通</span>
      </div>
      <p class="mt-2 text-sm leading-6 text-ink-2">你现在可以发布订单；发单后金额会进入托管，接单完成后由你审核结算。</p>
      <div class="mt-5 flex flex-wrap gap-3">
        <!-- 有受拦截的站内目标时继续前往该页（不自动跳转，先让用户看清权限状态） -->
        <router-link :to="redirectTarget || { name: 'order-create' }" class="btn-primary min-h-[44px] !px-5">
          {{ redirectTarget ? '继续发布订单' : '去发布订单' }}
        </router-link>
        <router-link :to="{ name: 'orders' }" class="btn-secondary min-h-[44px] !px-5">我的派单</router-link>
      </div>
    </section>

    <template v-else>
      <div v-if="redirectTarget" class="message-info">
        你刚才访问的页面需要发单权限，开通后可继续访问刚才的页面。
      </div>

      <!-- 最近一次申请记录 -->
      <section v-if="latest" class="surface-card p-6 sm:p-8">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-xl font-semibold text-ink-1">最近一次申请</h2>
          <span :class="['tag', statusTagClass]">{{ statusLabel }}</span>
        </div>

        <dl class="mt-4 grid gap-3 text-sm sm:grid-cols-2">
          <div class="rounded-tile border border-line-1 bg-surface-2 p-3">
            <dt class="text-xs text-ink-3">申请时间</dt>
            <dd class="mt-1 tabular-nums text-ink-1">{{ formatDateTime(latest.created_at) }}</dd>
          </div>
          <div class="rounded-tile border border-line-1 bg-surface-2 p-3">
            <dt class="text-xs text-ink-3">历史申请次数</dt>
            <dd class="mt-1 tabular-nums text-ink-1">{{ Number(latest.history_count ?? 0) }} 次</dd>
          </div>
          <div v-if="latest.resolved_at" class="rounded-tile border border-line-1 bg-surface-2 p-3">
            <dt class="text-xs text-ink-3">处理时间</dt>
            <dd class="mt-1 tabular-nums text-ink-1">{{ formatDateTime(latest.resolved_at) }}</dd>
          </div>
          <div v-if="latest.reviewed_by_name" class="rounded-tile border border-line-1 bg-surface-2 p-3">
            <dt class="text-xs text-ink-3">审核人</dt>
            <dd class="mt-1 text-ink-1">{{ latest.reviewed_by_name }}</dd>
          </div>
        </dl>

        <p v-if="latest.reason" class="mt-4 text-sm leading-6 text-ink-2">申请理由：{{ latest.reason }}</p>
        <p v-if="latest.decision_reason" class="mt-2 text-sm leading-6 text-ink-2">审核说明：{{ latest.decision_reason }}</p>

        <!-- 待审核：可撤回（两步确认） -->
        <div v-if="isPending" class="mt-5 rounded-tile border border-line-1 bg-surface-2 p-4">
          <p class="text-sm leading-6 text-ink-2">申请正在等待管理员审核，审核通过后即可发单。</p>
          <button
            type="button"
            class="btn-ghost mt-3 min-h-[44px] !px-4 text-danger"
            :disabled="store.submitting"
            @click="withdrawLatest"
          >
            {{ confirmWithdraw ? '确认撤回申请？' : '撤回申请' }}
          </button>
          <button
            v-if="confirmWithdraw"
            type="button"
            class="btn-secondary ml-2 mt-3 min-h-[44px] !px-4"
            :disabled="store.submitting"
            @click="confirmWithdraw = false"
          >
            取消
          </button>
        </div>
      </section>

      <!-- 申请表单：待审核时不显示，其余状态可重新申请 -->
      <section v-if="canApply" class="surface-card p-6 sm:p-8">
        <h2 class="text-xl font-semibold text-ink-1">{{ latest ? '重新申请发单权限' : '申请发单权限' }}</h2>
        <p class="mt-2 text-sm leading-6 text-ink-2">
          填写申请理由（可选，最多 500 字），提交后由管理员审核。{{ authStore.canAccept ? '审核通过前仍可接单，暂不能发单。' : '接单权限也未开通，请联系管理员；审核通过前暂不能发单。' }}
        </p>
        <form class="mt-5 space-y-4" @submit.prevent="submitApply">
          <div>
            <label class="label" for="publish-permission-reason">申请理由（可选）</label>
            <textarea
              id="publish-permission-reason"
              v-model="reason"
              rows="5"
              maxlength="500"
              class="input resize-none"
              placeholder="简单说明你的发单需求，例如：长期在平台派单，需要发布代练需求。"
            ></textarea>
            <p class="mt-1 text-xs tabular-nums text-ink-3">{{ reason.length }}/500</p>
          </div>
          <button type="submit" class="btn-primary min-h-[44px] !px-6" :disabled="store.submitting">
            {{ store.submitting ? '提交中...' : '提交申请' }}
          </button>
        </form>
      </section>

      <!-- 自助申请关闭：只提示联系管理员 -->
      <section v-else-if="!isPending" class="surface-card p-6 sm:p-8">
        <h2 class="text-xl font-semibold text-ink-1">未开启自助申请</h2>
        <p class="mt-2 text-sm leading-6 text-ink-2">当前站点未开启发单权限自助申请，请联系管理员开通。</p>
      </section>
    </template>

    <!-- 列表请求失败但仍保留上次结果：给出重试入口 -->
    <div v-if="store.error" class="message-error flex flex-wrap items-center gap-x-3 gap-y-1">
      <span class="min-w-0 flex-1">{{ store.error }}</span>
      <button type="button" class="filter-pill shrink-0" :disabled="store.loading" @click="loadMine">
        {{ store.loading ? '加载中…' : '重试' }}
      </button>
    </div>
  </div>
</template>
