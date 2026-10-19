<script setup>
/**
 * 后台「支付记录」：平台所有用户的充值（支付）记录。
 *
 * 老板要能在这一个页面看清两件事：
 * 1. 谁发起了支付（商户单号 / 用户 / 金额 / 时间）；
 * 2. 支付到底回调成功没有（状态 + 上游单号 + 回调原文）。
 * 并对单条记录人工改判成功 / 失败——金额增减由后端按状态机裁定，
 * 前端只负责把「这次操作会对谁的钱做什么」讲清楚，避免误点。
 */
import { computed, onMounted, reactive, ref } from 'vue'

import {
  RECHARGE_STATUS_OPTIONS,
  getRechargeStatusMeta,
  useWalletStore,
} from '@/stores/wallet'
import { formatCount, formatDateTime, formatPrice } from '@/utils/display'

const store = useWalletStore()

const statusFilter = ref('')
const search = ref('')
const notice = ref({ type: '', text: '' })
const submittingKey = ref('')

// 改判弹窗：target=SUCCESS / FAILED
const decision = ref(null)

const records = computed(() => store.adminRecharges)
const pagination = computed(() => store.adminRechargesPagination)
const stats = computed(() => store.adminRechargeStats)
const loading = computed(() => store.adminRechargesLoading)

const statCards = computed(() => [
  { label: '充值单总数', value: formatCount(stats.value.total), tone: 'text-ink-1' },
  { label: '已到账', value: formatCount(stats.value.success), tone: 'text-success' },
  { label: '待支付', value: formatCount(stats.value.pending), tone: 'text-warning' },
  { label: '失败 / 已关闭', value: formatCount(stats.value.failed), tone: 'text-danger' },
  { label: '已到账金额', value: formatPrice(stats.value.success_amount), tone: 'text-price' },
])

function noticeClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

function statusMeta(status) {
  return getRechargeStatusMeta(status)
}

// 回调是否真的成功：上游单号是回调成功回填的，最直接的证据
function callbackLabel(item) {
  if (item.epay_trade_no) return '回调成功'
  if (item.status === 'SUCCESS') return '人工置成功'
  if (item.status === 'FAILED') return '未回调'
  if (item.status === 'CLOSED') return '已关闭'
  return '等待回调'
}

function callbackClass(item) {
  if (item.epay_trade_no) return '!bg-success-soft !text-success'
  if (item.status === 'SUCCESS') return '!bg-info-soft !text-info'
  if (item.status === 'FAILED' || item.status === 'CLOSED') {
    return '!bg-danger-soft !text-danger'
  }
  return '!bg-surface-3 !text-ink-2'
}

function methodLabel(type) {
  if (type === 'alipay') return '支付宝'
  if (type === 'wxpay') return '微信'
  return type || '-'
}

function userName(item) {
  return item.user?.username || (item.user?.id != null ? `用户 #${item.user.id}` : '未知用户')
}

async function load(page = 1) {
  await store.fetchAdminRecharges({
    status: statusFilter.value || undefined,
    q: search.value.trim() || undefined,
    page,
  })
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

// ── 改判弹窗 ──

// 该记录当前状态下，两个按钮各自会有什么效果（用于在列表里提示，不是按钮文案）
function canMarkSuccess(item) {
  return item.status === 'PENDING' || item.status === 'FAILED'
}

function canMarkFailed(item) {
  return item.status === 'PENDING' || item.status === 'SUCCESS'
}

function openDecision(item, target) {
  const charge = Number(item.amount ?? 0)
  // 预先算清「这次操作到底动不动钱」，写在弹窗第一行，避免管理员误判
  let impact = ''
  if (target === 'SUCCESS') {
    impact = `将给 ${userName(item)} 的可用余额增加 ${formatPrice(charge)}`
  } else if (item.status === 'SUCCESS') {
    impact = `将从 ${userName(item)} 的可用余额扣回 ${formatPrice(charge)}（冲正）`
  } else {
    impact = '这笔充值尚未入账，改为失败只改状态，不动余额'
  }

  decision.value = reactive({
    item,
    target,
    impact,
    reason: '',
    error: '',
    submitting: false,
  })
}

function closeDecision() {
  if (decision.value?.submitting) return
  decision.value = null
}

async function submitDecision() {
  const state = decision.value
  if (!state) return
  state.error = ''

  const reason = state.reason.trim()
  // 改为失败必填原因（后端同样强制），避免无据可查的扣款
  if (state.target === 'FAILED' && !reason) {
    state.error = '请填写改为失败的原因'
    return
  }

  state.submitting = true
  submittingKey.value = `recharge-${state.item.id}`
  const result = await store.setAdminRechargeStatus(
    state.item.id,
    state.target,
    reason || undefined
  )
  state.submitting = false
  submittingKey.value = ''

  if (!result.success) {
    state.error = result.error || '操作失败'
    return
  }

  notice.value = {
    type: 'success',
    text:
      state.target === 'SUCCESS'
        ? `已改为成功：${userName(state.item)} 到账 ${formatPrice(state.item.amount)}`
        : `已改为失败：${state.item.trade_no}`,
  }
  decision.value = null
  // 改判会改变金额与统计，列表和统计条一起刷新，停留在当前页
  await load(pagination.value.page)
}

onMounted(() => {
  load(1)
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <header class="flex flex-col gap-1">
      <h2 class="text-2xl font-semibold text-ink-1">支付记录</h2>
      <p class="text-sm text-ink-2">
        平台所有用户的充值（支付）记录。可查看每笔支付是否回调成功，并人工改判为成功或失败。
      </p>
    </header>

    <!-- 统计条：全量口径，不随下方筛选变化 -->
    <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <article
        v-for="card in statCards"
        :key="card.label"
        class="rounded-tile border border-line-1 bg-surface-2 px-4 py-3"
      >
        <p class="text-xs text-ink-3">{{ card.label }}</p>
        <p class="mt-1 text-xl font-semibold tabular-nums" :class="card.tone">{{ card.value }}</p>
      </article>
    </div>

    <div v-if="notice.text" class="mt-4" :class="noticeClass(notice.type)">{{ notice.text }}</div>

    <!-- 筛选 -->
    <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center">
      <input
        v-model="search"
        type="search"
        class="input min-h-[44px] sm:max-w-xs"
        placeholder="商户单号、上游单号、用户名或邮箱"
        aria-label="搜索支付记录"
        @keyup.enter="handleSearch"
      />
      <select v-model="statusFilter" class="input min-h-[44px] sm:max-w-[160px]" aria-label="状态筛选">
        <option v-for="option in RECHARGE_STATUS_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
      <button type="button" class="btn-secondary min-h-[44px] !px-4" @click="handleSearch">筛选</button>
      <button type="button" class="btn-ghost min-h-[44px] !px-4" @click="resetFilters">重置</button>
    </div>

    <!-- 加载骨架 -->
    <div v-if="loading" class="mt-5 space-y-3" aria-busy="true">
      <div v-for="n in 4" :key="`recharge-skeleton-${n}`" class="skeleton h-16 !rounded-tile"></div>
    </div>

    <!-- 空态 -->
    <div v-else-if="!records.length" class="empty-state mt-6">
      <div class="empty-state__icon" aria-hidden="true">💳</div>
      <h3 class="empty-state__title">
        {{ search || statusFilter ? '没有符合条件的支付记录' : '暂无支付记录' }}
      </h3>
      <p class="empty-state__copy">
        {{ search || statusFilter ? '换个关键词或状态试试。' : '用户在我的钱包发起充值后，记录会出现在这里。' }}
      </p>
    </div>

    <template v-else>
      <!-- 桌面端：表格 -->
      <div class="mt-5 hidden overflow-x-auto sm:block">
        <table class="data-table !min-w-[1040px]">
          <thead>
            <tr>
              <th>商户单号</th>
              <th>用户</th>
              <th class="text-right">金额</th>
              <th>支付方式</th>
              <th>状态</th>
              <th>回调</th>
              <th>发起时间</th>
              <th>到账 / 改判</th>
              <th class="text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in records" :key="item.id">
              <td>
                <p class="font-mono text-xs text-ink-1">{{ item.trade_no }}</p>
                <p v-if="item.epay_trade_no" class="mt-0.5 font-mono text-[11px] text-ink-3">
                  上游 {{ item.epay_trade_no }}
                </p>
              </td>
              <td>
                <p class="text-sm text-ink-1">{{ userName(item) }}</p>
                <p v-if="item.user" class="mt-0.5 text-[11px] text-ink-3">#{{ item.user.id }} · {{ item.user.email }}</p>
              </td>
              <td class="text-right font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</td>
              <td class="text-sm text-ink-2">{{ methodLabel(item.payment_method) }}</td>
              <td>
                <span :class="['tag', statusMeta(item.status).tagClass]">{{ statusMeta(item.status).label }}</span>
              </td>
              <td>
                <span :class="['tag', callbackClass(item)]">{{ callbackLabel(item) }}</span>
              </td>
              <td class="text-xs tabular-nums text-ink-2">{{ formatDateTime(item.created_at) }}</td>
              <td class="text-xs tabular-nums text-ink-2">
                <p v-if="item.marked_at">
                  <span class="text-ink-1">{{ formatDateTime(item.marked_at) }}</span>
                  <span class="ml-1 text-ink-3">人工</span>
                  <span v-if="item.marked_by" class="text-ink-3">· {{ item.marked_by.username }}</span>
                </p>
                <p v-else-if="item.paid_at">{{ formatDateTime(item.paid_at) }}</p>
                <p v-else class="text-ink-3">—</p>
                <p v-if="item.admin_note" class="mt-0.5 max-w-[180px] truncate text-[11px] text-ink-3" :title="item.admin_note">
                  {{ item.admin_note }}
                </p>
              </td>
              <td>
                <div class="flex flex-wrap justify-end gap-1.5">
                  <button
                    type="button"
                    class="btn-secondary !px-3 !py-1.5 text-xs"
                    :disabled="!canMarkSuccess(item) || Boolean(submittingKey)"
                    :title="canMarkSuccess(item) ? '改为成功并把金额入账' : '当前状态不能改为成功'"
                    @click="openDecision(item, 'SUCCESS')"
                  >改为成功</button>
                  <!-- 「改为失败」在已入账时是扣钱操作，按项目惯例用危险色而非文字链接，
                       避免在表格里被当成普通导航而误点 -->
                  <button
                    type="button"
                    :class="item.status === 'SUCCESS' ? 'btn-danger' : 'btn-secondary'"
                    class="!px-3 !py-1.5 text-xs"
                    :disabled="!canMarkFailed(item) || Boolean(submittingKey)"
                    :title="canMarkFailed(item) ? '改为失败（已入账的会冲正扣回）' : '当前状态不能改为失败'"
                    @click="openDecision(item, 'FAILED')"
                  >改为失败</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 移动端：卡片 -->
      <div class="mt-5 space-y-3 sm:hidden">
        <article
          v-for="item in records"
          :key="item.id"
          class="rounded-tile border border-line-1 bg-surface-2 p-4"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-ink-1">{{ userName(item) }}</p>
              <p class="mt-0.5 truncate font-mono text-[11px] text-ink-3">{{ item.trade_no }}</p>
            </div>
            <p class="shrink-0 text-lg font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</p>
          </div>
          <div class="mt-2 flex flex-wrap items-center gap-1.5">
            <span :class="['tag', statusMeta(item.status).tagClass]">{{ statusMeta(item.status).label }}</span>
            <span :class="['tag', callbackClass(item)]">{{ callbackLabel(item) }}</span>
            <span class="tag !bg-surface-3 !text-ink-2">{{ methodLabel(item.payment_method) }}</span>
          </div>
          <p class="mt-2 text-xs tabular-nums text-ink-3">
            发起 {{ formatDateTime(item.created_at) }}
            <template v-if="item.marked_at"> · 人工改判 {{ formatDateTime(item.marked_at) }}</template>
            <template v-else-if="item.paid_at"> · 到账 {{ formatDateTime(item.paid_at) }}</template>
          </p>
          <p v-if="item.admin_note" class="mt-1 text-xs text-ink-2">原因：{{ item.admin_note }}</p>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              class="btn-secondary min-h-[44px] !px-4 !py-2"
              :disabled="!canMarkSuccess(item) || Boolean(submittingKey)"
              @click="openDecision(item, 'SUCCESS')"
            >改为成功</button>
            <button
              type="button"
              :class="item.status === 'SUCCESS' ? 'btn-danger' : 'btn-secondary'"
              class="min-h-[44px] !px-4 !py-2"
              :disabled="!canMarkFailed(item) || Boolean(submittingKey)"
              @click="openDecision(item, 'FAILED')"
            >改为失败</button>
          </div>
        </article>
      </div>

      <!-- 分页 -->
      <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p class="text-sm tabular-nums text-ink-2">
          第 {{ pagination.page }} / {{ pagination.pages || 1 }} 页，共 {{ pagination.total }} 笔
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
    </template>

    <!-- 改判确认弹窗：把「动谁的钱、动多少」写在最前面 -->
    <div
      v-if="decision"
      class="modal-backdrop"
      role="presentation"
      @click.self="closeDecision"
    >
      <div class="modal-card modal-sheet !max-w-[480px]" role="dialog" aria-modal="true" aria-label="改判支付记录">
        <h3 class="text-lg font-semibold text-ink-1">
          {{ decision.target === 'SUCCESS' ? '改为成功' : '改为失败' }}
        </h3>
        <p class="mt-1 font-mono text-xs text-ink-3">{{ decision.item.trade_no }}</p>

        <div class="mt-4 rounded-tile border border-line-1 bg-surface-2 p-3">
          <p class="text-sm font-medium text-ink-1">{{ decision.impact }}</p>
          <p class="mt-1 text-xs text-ink-2">
            用户 {{ userName(decision.item) }} · 金额 {{ formatPrice(decision.item.amount) }}
          </p>
        </div>

        <div class="mt-4">
          <label class="label" for="recharge-decision-reason">
            {{ decision.target === 'FAILED' ? '失败原因（必填）' : '备注（可选）' }}
          </label>
          <textarea
            id="recharge-decision-reason"
            v-model="decision.reason"
            class="input min-h-[80px]"
            :placeholder="decision.target === 'FAILED' ? '例如：上游确认未收到款' : '例如：客户已付款，补单'"
            maxlength="255"
          ></textarea>
          <p class="mt-1 text-xs text-ink-3">操作人与时间会记录在这条支付记录上，便于日后核对。</p>
        </div>

        <div v-if="decision.error" class="message-error mt-3">{{ decision.error }}</div>

        <div class="mt-5 flex flex-wrap justify-end gap-2">
          <button type="button" class="btn-ghost min-h-[44px] !px-4" :disabled="decision.submitting" @click="closeDecision">
            取消
          </button>
          <button
            type="button"
            :class="decision.target === 'FAILED' ? 'btn-danger' : 'btn-primary'"
            class="min-h-[44px] !px-5"
            :disabled="decision.submitting"
            @click="submitDecision"
          >{{ decision.submitting ? '处理中...' : '确认' }}</button>
        </div>
      </div>
    </div>
  </section>
</template>
