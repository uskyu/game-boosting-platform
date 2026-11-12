<script setup>
/**
 * 后台「发单申请」：普通用户提交的发单权限申请列表与审核。
 * 独立组件自取数据，避免继续膨胀 AdminView 内联 section。
 */
import { computed, onMounted, ref, watch } from 'vue'

import {
  usePublishPermissionStore,
  PUBLISH_PERMISSION_STATUS_OPTIONS,
  getPublishPermissionStatusLabel,
  getPublishPermissionStatusTagClass,
} from '@/stores/publishPermission'
import { formatCount, formatDateTime } from '@/utils/display'

const store = usePublishPermissionStore()

const statusFilter = ref('PENDING')
const search = ref('')
const notice = ref({ type: '', text: '' })
// 审核弹窗：type=approve（二次确认）/ reject（必填原因）
const reviewModal = ref(null)

const requests = computed(() => store.adminRequests)
const pagination = computed(() => store.adminPagination)
const loading = computed(() => store.loading)

function noticeClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  if (type === 'warning') return 'message-warning'
  return 'message-info'
}

function applicantName(item) {
  return item?.username || (item?.user_id != null ? `用户 #${item.user_id}` : '未知用户')
}

async function load(page = 1) {
  const result = await store.fetchAdminRequests({
    status: statusFilter.value || undefined,
    q: search.value.trim() || undefined,
    page,
  })
  if (!result.success) return
  // 审核后总数变少：当前页可能已超出新的总页数，回落到最后一页。
  // pages 为 0（空结果）时不回落，避免无意义递归。
  const pages = Number(store.adminPagination.pages) || 0
  if (page > 1 && pages > 0 && page > pages) {
    await load(pages)
  }
}

function handleSearch() {
  load(1)
}

function resetFilters() {
  statusFilter.value = ''
  search.value = ''
  load(1)
}

function handlePage(page) {
  if (page < 1 || page > (pagination.value.pages || 1)) return
  load(page)
}

function openApprove(item) {
  // 被封禁用户即使申请通过也无法发单；后端也会返回 400，这里先拦一道。
  if (item?.publish_banned === true) {
    notice.value = { type: 'warning', text: '该用户已被封禁发单，请先到用户管理解除封禁' }
    return
  }
  reviewModal.value = { type: 'approve', item, reason: '', error: '', submitting: false }
}

function openReject(item) {
  reviewModal.value = { type: 'reject', item, reason: '', error: '', submitting: false }
}

function closeReviewModal() {
  if (reviewModal.value?.submitting) return
  reviewModal.value = null
}

async function submitReview() {
  const state = reviewModal.value
  if (!state || state.submitting) return
  state.error = ''

  const reason = state.reason.trim()
  if (state.type === 'reject' && !reason) {
    state.error = '请填写驳回原因'
    return
  }

  state.submitting = true
  const result = await store.reviewRequest(state.item.id, {
    action: state.type === 'approve' ? 'approve' : 'reject',
    reason: reason || undefined,
  })
  state.submitting = false
  if (!result.success) {
    state.error = result.error || '操作失败'
    return
  }

  notice.value = {
    type: 'success',
    text: state.type === 'approve' ? '已通过发单申请' : '已驳回发单申请',
  }
  reviewModal.value = null
  // 审核后状态变化会影响当前筛选列表，刷新并停留在当前页
  await load(pagination.value.page)
}

onMounted(() => {
  // 先清空上一管理员/账号留下的列表与审核状态，再拉取；同时置 loading 避免闪空
  store.reset()
  store.loading = true
  load(1)
})

watch(statusFilter, () => load(1))
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <header class="flex flex-col gap-1">
      <h2 class="text-2xl font-semibold text-ink-1">发单申请</h2>
      <p class="text-sm text-ink-2">
        查看普通用户提交的发单权限申请并审核。驳回必须填写原因；通过后仅开通发单权限，不改动接单权限与历史订单。用户端会自动刷新权限，若按钮未更新可到「发单权限」页点「刷新权限」。
      </p>
    </header>

    <div v-if="notice.text" class="mt-4" :class="noticeClass(notice.type)">{{ notice.text }}</div>

    <!-- 列表加载失败：保留旧数据并给出重试入口 -->
    <div v-if="store.error && !loading" class="message-error mt-4 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span class="min-w-0 flex-1">{{ store.error }}</span>
      <button type="button" class="filter-pill shrink-0" @click="load(pagination.page)">重试</button>
    </div>

    <!-- 筛选 -->
    <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center">
      <input
        v-model="search"
        type="search"
        class="input min-h-[44px] sm:max-w-xs"
        placeholder="用户名、邮箱或用户 ID"
        aria-label="搜索发单申请"
        @keyup.enter="handleSearch"
      />
      <select v-model="statusFilter" class="input min-h-[44px] sm:max-w-[160px]" aria-label="申请状态筛选">
        <option
          v-for="option in PUBLISH_PERMISSION_STATUS_OPTIONS"
          :key="option.value"
          :value="option.value"
        >{{ option.label }}</option>
      </select>
      <button type="button" class="btn-secondary min-h-[44px] !px-4" @click="handleSearch">筛选</button>
      <button type="button" class="btn-ghost min-h-[44px] !px-4" @click="resetFilters">重置</button>
    </div>

    <!-- 加载骨架 -->
    <div v-if="loading" class="mt-5 space-y-3" aria-busy="true">
      <div v-for="n in 4" :key="`publish-request-skeleton-${n}`" class="skeleton h-28 !rounded-card"></div>
    </div>

    <!-- 空态 -->
    <div v-else-if="!requests.length" class="empty-state mt-6">
      <div class="empty-state__icon" aria-hidden="true">📝</div>
      <h3 class="empty-state__title">
        {{ store.error ? '加载失败' : (search || statusFilter ? '没有符合条件的申请' : '暂无发单申请') }}
      </h3>
      <p class="empty-state__copy">
        {{ store.error ? '请检查网络后点击上方「重试」。' : (search || statusFilter ? '换个关键词或状态试试。' : '用户提交发单权限申请后，会出现在这里。') }}
      </p>
    </div>

    <div v-else class="mt-5 space-y-3">
      <article
        v-for="item in requests"
        :key="item.id"
        class="catalog-card cyber-corner"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-lg font-semibold text-ink-1">#{{ item.id }} {{ applicantName(item) }}</h3>
              <span :class="['tag', getPublishPermissionStatusTagClass(item.status)]">
                {{ getPublishPermissionStatusLabel(item.status) }}
              </span>
              <span v-if="item.publish_banned" class="tag !bg-danger-soft !text-danger">封禁中</span>
            </div>
            <p class="mt-1 text-xs text-ink-3">
              {{ item.email || '未填写邮箱' }} · 用户 #{{ item.user_id }}
              <span v-if="item.user_created_at"> · 注册于 {{ formatDateTime(item.user_created_at) }}</span>
            </p>
            <p class="mt-1 text-xs tabular-nums text-ink-3">
              申请于 {{ formatDateTime(item.created_at) }} · 历史申请
              <span class="font-semibold text-ink-2">{{ Number(item.history_count ?? 0) }}</span> 次
            </p>
          </div>
        </div>

        <div v-if="item.reason" class="mt-3 rounded-tile border border-line-1 bg-surface-2 p-3">
          <p class="text-xs text-ink-3">申请理由</p>
          <p class="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-ink-1">{{ item.reason }}</p>
        </div>
        <div v-else class="mt-3 rounded-tile border border-dashed border-line-1 px-3 py-2 text-xs text-ink-3">
          未填写申请理由
        </div>

        <p v-if="item.decision_reason" class="mt-2 text-sm leading-6 text-ink-2">
          审核说明：{{ item.decision_reason }}
        </p>
        <p v-if="item.reviewed_by_name || item.resolved_at" class="mt-1 text-xs text-ink-3">
          <span v-if="item.reviewed_by_name">审核人 {{ item.reviewed_by_name }}</span>
          <span v-if="item.reviewed_by_name && item.resolved_at"> · </span>
          <span v-if="item.resolved_at">处理于 {{ formatDateTime(item.resolved_at) }}</span>
        </p>

        <p v-if="item.status === 'PENDING' && item.publish_banned" class="message-warning mt-3 text-sm leading-6">
          该用户已被封禁发单，请先到用户管理解除封禁
        </p>
        <div v-if="item.status === 'PENDING'" class="mt-4 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            class="btn-primary min-h-[44px] !px-4 !py-2"
            :disabled="item.publish_banned === true"
            :title="item.publish_banned === true ? '该用户已被封禁发单，请先到用户管理解除封禁' : ''"
            @click="openApprove(item)"
          >通过</button>
          <button type="button" class="btn-danger min-h-[44px] !px-4 !py-2" @click="openReject(item)">驳回</button>
        </div>
      </article>

      <!-- 分页 -->
      <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm tabular-nums text-ink-2">
          第 {{ pagination.page }} / {{ pagination.pages || 1 }} 页，共 {{ formatCount(pagination.total) }} 条
        </p>
        <div class="flex gap-2">
          <button
            type="button"
            class="btn-secondary min-h-[44px] !px-4"
            :disabled="pagination.page <= 1"
            @click="handlePage(pagination.page - 1)"
          >上一页</button>
          <button
            type="button"
            class="btn-secondary min-h-[44px] !px-4"
            :disabled="pagination.page >= (pagination.pages || 1)"
            @click="handlePage(pagination.page + 1)"
          >下一页</button>
        </div>
      </div>
    </div>

    <!-- 审核弹窗：通过二次确认 / 驳回必填原因 -->
    <div v-if="reviewModal" class="modal-scrim" role="presentation" @click.self="closeReviewModal">
      <div class="modal-card modal-sheet !max-w-[480px]" role="dialog" aria-modal="true" aria-label="审核发单申请">
        <h3 class="text-lg font-semibold text-ink-1">
          {{ reviewModal.type === 'approve' ? '通过发单申请' : '驳回发单申请' }}
        </h3>
        <p class="mt-1 text-sm text-ink-2">
          {{ applicantName(reviewModal.item) }} · 用户 #{{ reviewModal.item.user_id }}
        </p>

        <div v-if="reviewModal.type === 'approve'" class="message-info mt-4 text-sm leading-6">
          通过后仅开通该用户的发单权限，不改动接单权限与历史订单。用户端会自动刷新权限，若按钮未更新可到「发单权限」页点「刷新权限」。
        </div>

        <div v-if="reviewModal.type === 'reject'" class="mt-4">
          <label class="label" for="publish-request-reject-reason">驳回原因（必填，最多 500 字）</label>
          <textarea
            id="publish-request-reject-reason"
            v-model="reviewModal.reason"
            rows="4"
            maxlength="500"
            class="input resize-none"
            placeholder="请说明驳回原因，用户会在申请页看到"
          ></textarea>
        </div>

        <div v-if="reviewModal.error" class="message-error mt-3">{{ reviewModal.error }}</div>

        <div class="mt-5 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            class="btn-ghost min-h-[44px] !px-4"
            :disabled="reviewModal.submitting"
            @click="closeReviewModal"
          >取消</button>
          <button
            type="button"
            :class="reviewModal.type === 'approve' ? 'btn-primary' : 'btn-danger'"
            class="min-h-[44px] !px-5"
            :disabled="reviewModal.submitting"
            @click="submitReview"
          >
            {{ reviewModal.submitting ? '处理中...' : (reviewModal.type === 'approve' ? '确认通过' : '确认驳回') }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
