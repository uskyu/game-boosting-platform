<script setup>
import { computed, onMounted, ref } from 'vue'

import { useWithdrawalPayoutStore } from '@/stores/withdrawalPayout'
import { WITHDRAWAL_STATUS_META } from '@/stores/wallet'
import { PAYOUT_RESULT_META, getPayoutCategoryMeta } from '@/utils/withdrawalCategories'
import { formatDateTime, formatPrice } from '@/utils/display'

const payoutStore = useWithdrawalPayoutStore()

// 同一时刻只展开一个批次明细；importResult 记住最近一次回执导入结果（按 batch_id 归属卡片）
const expandedId = ref(null)
const notice = ref({ type: '', text: '' })
const importResult = ref(null)
// 导出 / 导入共用 store.submitting，用本地 key 区分是哪个按钮在转圈
const actionKey = ref('')

const batches = computed(() => payoutStore.batches)
const pagination = computed(() => payoutStore.batchesPagination)
const busy = computed(
  () => Boolean(actionKey.value) || payoutStore.submitting || payoutStore.currentBatchLoading
)

function noticeClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

// OPEN=待导回执（警示色），CLOSED=已导回执（成功色），与提现状态标签同一套语义色
function batchStatusMeta(status) {
  if (status === 'CLOSED') {
    return { label: '已导回执', tagClass: '!bg-success-soft !text-success' }
  }
  return { label: '待导回执', tagClass: '!bg-warning-soft !text-warning' }
}

function categoryLabel(category) {
  return getPayoutCategoryMeta(category)?.label || category || '未知分类'
}

function payoutResultLabel(result) {
  return PAYOUT_RESULT_META[result]?.label || '-'
}

function payoutResultClass(result) {
  if (result === 'PAID') return 'text-success'
  if (result === 'REJECTED' || result === 'UNMATCHED') return 'text-danger'
  return 'text-ink-2'
}

function withdrawalStatusMeta(status) {
  return WITHDRAWAL_STATUS_META[status] || { label: status || '未知状态', tagClass: '!bg-surface-3 !text-ink-2' }
}

// 回执行的「状态」列口径：成功 / 失败 / 其他（未处理或未匹配）
// 后端按回执原文语义下发（成功/失败/其他）；兼容早期 PAID/REJECTED 写法
function receiptStatusText(item) {
  if (item?.status === 'PAID' || item?.status === '成功') return '成功'
  if (item?.status === 'REJECTED' || item?.status === '失败') return '失败'
  return '其他'
}

// 未匹配 / 已驳回的行整行标红，和 message-error 一个色系
function receiptRowClass(item) {
  return item?.result === 'UNMATCHED' || item?.result === 'REJECTED' ? 'bg-danger-soft' : ''
}

function receiptAmountText(value) {
  return value == null || value === '' ? '-' : formatPrice(value)
}

// 只有拉过明细才知道还剩几笔可导出；没拉明细时不禁用，让后端把具体原因透出来
function isExportDisabled(batch) {
  const detail = payoutStore.currentBatch
  return expandedId.value === batch.id && detail?.id === batch.id && Number(detail.exportable_count) === 0
}

async function handleExport(batch) {
  notice.value = { type: '', text: '' }
  payoutStore.clearError()
  actionKey.value = `export-${batch.id}`
  const result = await payoutStore.exportBatch(batch.id)
  actionKey.value = ''
  if (result.success) {
    notice.value = { type: 'success', text: `已导出付款文件：${result.filename || '付款文件.xls'}` }
  } else {
    notice.value = { type: 'error', text: result.error || '导出付款文件失败' }
  }
}

async function handleImportReceipt(batch, event) {
  const file = event.target.files?.[0]
  // 重置 input：同一个文件连续选两次也能再次触发 change
  event.target.value = ''
  if (!file) return
  notice.value = { type: '', text: '' }
  payoutStore.clearError()
  actionKey.value = `import-${batch.id}`
  const result = await payoutStore.importReceipt(batch.id, file)
  if (!result.success) {
    actionKey.value = ''
    notice.value = { type: 'error', text: result.error || '导入回执失败' }
    return
  }
  importResult.value = result.data || null
  // 回执会改写提现单状态与批次快照：明细和列表都要刷新（列表停留当前页）
  await Promise.all([
    payoutStore.fetchBatch(batch.id),
    payoutStore.fetchBatches({ page: pagination.value.page }),
  ])
  actionKey.value = ''
}

async function toggleDetail(batch) {
  if (expandedId.value === batch.id) {
    expandedId.value = null
    return
  }
  expandedId.value = batch.id
  notice.value = { type: '', text: '' }
  const result = await payoutStore.fetchBatch(batch.id)
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '加载批次明细失败' }
  }
}

function closeImportResult() {
  importResult.value = null
}

function handlePage(page) {
  if (page < 1 || page > pagination.value.pages || page === pagination.value.page) return
  // 翻页后展开态与导入结果都不再对应当前页数据，一并收起
  expandedId.value = null
  importResult.value = null
  payoutStore.fetchBatches({ page })
}

onMounted(() => {
  payoutStore.clearError()
  payoutStore.fetchBatches({ page: 1 })
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6 lg:p-8">
    <h2 class="text-2xl font-semibold text-ink-1">打款批次</h2>
    <p class="mt-2 text-sm text-ink-2">
      按渠道把提现单打包成批次：导出付款文件上传到支付宝 / 微信后台，再把渠道回执导回来自动核销（成功即打款、失败即驳回并退额）。
    </p>

    <div v-if="notice.text" class="mt-4" :class="noticeClass(notice.type)">{{ notice.text }}</div>

    <div v-if="payoutStore.batchesLoading" class="mt-6 space-y-3" aria-busy="true">
      <div v-for="n in 3" :key="`batch-skeleton-${n}`" class="skeleton h-44 !rounded-card"></div>
    </div>

    <div v-else-if="!batches.length" class="empty-state mt-6">
      <div class="empty-state__icon" aria-hidden="true">🧾</div>
      <h3 class="empty-state__title">暂无打款批次</h3>
      <p class="empty-state__copy">在「提现处理」勾选提现单即可创建。</p>
    </div>

    <div v-else class="mt-6 space-y-4">
      <article v-for="batch in batches" :key="batch.id" class="catalog-card cyber-corner">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-xl font-semibold text-ink-1">#{{ batch.id }} {{ categoryLabel(batch.category) }}分类</h3>
              <span class="tag" :class="batchStatusMeta(batch.status).tagClass">{{ batchStatusMeta(batch.status).label }}</span>
            </div>
            <p class="mt-2 text-sm text-ink-2">{{ batch.remark || '（无备注）' }}</p>
            <p class="mt-1 text-xs text-ink-3">创建于 {{ formatDateTime(batch.created_at) }} · 共 {{ batch.item_count }} 笔</p>
          </div>
          <p class="shrink-0 text-2xl font-semibold tabular-nums text-price">{{ formatPrice(batch.total_amount) }}</p>
        </div>

        <!-- 已导回执：导入时间 / 渠道批次号 / 成功与失败快照 -->
        <div v-if="batch.status === 'CLOSED'" class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div class="info-tile">
            <p class="info-tile__label">导入时间</p>
            <p class="info-tile__value">{{ formatDateTime(batch.imported_at) }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">{{ categoryLabel(batch.category) }}批次订单号</p>
            <p class="info-tile__value break-words">{{ batch.reference_no || '-' }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">打款成功</p>
            <p class="info-tile__value tabular-nums text-success">{{ batch.success_count ?? 0 }} 笔 · {{ formatPrice(batch.success_amount) }}</p>
          </div>
          <div class="info-tile">
            <p class="info-tile__label">打款失败</p>
            <p class="info-tile__value tabular-nums text-danger">{{ batch.fail_count ?? 0 }} 笔 · {{ formatPrice(batch.fail_amount) }}</p>
          </div>
        </div>

        <div class="mt-5 flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="btn-secondary min-h-[44px] !px-4 !py-2"
            :disabled="busy || isExportDisabled(batch)"
            :title="isExportDisabled(batch) ? '暂无可导出的提现（均已处理或已驳回）' : ''"
            @click="handleExport(batch)"
          >
            {{ actionKey === `export-${batch.id}` ? '导出中...' : '导出付款文件' }}
          </button>
          <!-- 隐藏 file input + 样式化 label（与游戏管理「上传 Logo」同一套写法） -->
          <label class="btn-secondary min-h-[44px] !px-4 !py-2" :class="{ 'pointer-events-none opacity-50': busy }">
            <input
              type="file"
              accept=".xls,.csv"
              class="sr-only"
              :disabled="busy"
              @change="handleImportReceipt(batch, $event)"
            />
            {{ actionKey === `import-${batch.id}` ? '导入中...' : '导入回执' }}
          </label>
          <button type="button" class="btn-ghost !px-4 !py-2" :disabled="busy" @click="toggleDetail(batch)">
            {{ expandedId === batch.id ? '收起明细' : '查看明细' }}
          </button>
        </div>

        <!-- 批次明细抽屉 -->
        <div v-if="expandedId === batch.id" class="mt-5">
          <div v-if="payoutStore.currentBatchLoading" class="skeleton h-32 !rounded-tile" aria-busy="true"></div>
          <template v-else-if="payoutStore.currentBatch?.id === batch.id">
            <p class="text-xs text-ink-3">
              仍可导出 {{ payoutStore.currentBatch.exportable_count ?? 0 }} 笔（待审核 / 待打款）；已打款、已驳回的提现不再导出。
            </p>
            <div class="mt-2 overflow-x-auto">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>提现单</th>
                    <th>用户</th>
                    <th>收款账号</th>
                    <th>收款人</th>
                    <th>金额</th>
                    <th>状态</th>
                    <th v-if="batch.status === 'CLOSED'">打款结果</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in payoutStore.currentBatch.items || []" :key="item.id">
                    <td class="font-semibold text-ink-1">W{{ item.id }}</td>
                    <td class="text-ink-1">{{ item.username || `用户 #${item.user_id}` }}</td>
                    <td class="break-words text-ink-2">{{ item.account_no || '-' }}</td>
                    <td class="text-ink-2">{{ item.account_name || '-' }}</td>
                    <td class="font-semibold tabular-nums text-price">{{ formatPrice(item.amount) }}</td>
                    <td>
                      <span class="tag !px-2.5 !py-0.5 !text-[11px]" :class="withdrawalStatusMeta(item.status).tagClass">
                        {{ withdrawalStatusMeta(item.status).label }}
                      </span>
                    </td>
                    <td v-if="batch.status === 'CLOSED'" :class="payoutResultClass(item.payout_result)">
                      {{ payoutResultLabel(item.payout_result) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="!(payoutStore.currentBatch.items || []).length" class="mt-2 text-sm text-ink-3">批次内暂无提现明细。</p>
          </template>
        </div>

        <!-- 回执导入结果 -->
        <div v-if="importResult && importResult.batch_id === batch.id" class="mt-5">
          <div class="flex flex-wrap items-center gap-2">
            <span class="tag !bg-success-soft !text-success">成功 {{ importResult.success }} 笔</span>
            <span class="tag !bg-danger-soft !text-danger">失败 {{ importResult.failed }} 笔</span>
            <span class="tag">跳过 {{ importResult.skipped }} 笔</span>
            <span class="tag !bg-warning-soft !text-warning">未匹配 {{ importResult.unmatched }} 笔</span>
            <span class="text-xs text-ink-3">回执共 {{ importResult.total_rows }} 行</span>
            <button type="button" class="btn-ghost ml-auto !px-3 !py-1.5 text-xs" @click="closeImportResult">收起结果</button>
          </div>

          <div v-if="(importResult.items || []).length" class="mt-3 overflow-x-auto">
            <table class="data-table">
              <thead>
                <tr>
                  <th>提现单</th>
                  <th>收款账号</th>
                  <th>姓名</th>
                  <th>金额</th>
                  <th>状态</th>
                  <th>回执结果</th>
                  <th>原因</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(item, index) in importResult.items"
                  :key="`${item.seq ?? 'row'}-${index}`"
                  :class="receiptRowClass(item)"
                >
                  <td class="font-semibold" :class="item.withdrawal_id ? 'text-ink-1' : 'text-danger'">
                    {{ item.withdrawal_id ? `W${item.withdrawal_id}` : '未匹配' }}
                  </td>
                  <td class="break-words text-ink-2">{{ item.account_no || '-' }}</td>
                  <td class="text-ink-2">{{ item.account_name || '-' }}</td>
                  <td class="font-semibold tabular-nums text-price">{{ receiptAmountText(item.amount) }}</td>
                  <td class="text-ink-2">{{ receiptStatusText(item) }}</td>
                  <td :class="payoutResultClass(item.result)">{{ payoutResultLabel(item.result) }}</td>
                  <td class="text-ink-3">{{ item.reason || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="mt-3 text-sm text-ink-3">回执里没有可处理的明细行。</p>
        </div>
      </article>
    </div>

    <div v-if="pagination.pages > 1" class="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <p class="text-sm text-ink-2">
        {{ pagination.page }} / {{ pagination.pages }} · 共 {{ pagination.total }} 个批次
      </p>
      <div class="flex items-center gap-2">
        <button
          class="btn-secondary !px-4 !py-2"
          :disabled="pagination.page <= 1 || payoutStore.batchesLoading"
          @click="handlePage(pagination.page - 1)"
        >
          上一页
        </button>
        <button
          class="btn-secondary !px-4 !py-2"
          :disabled="pagination.page >= pagination.pages || payoutStore.batchesLoading"
          @click="handlePage(pagination.page + 1)"
        >
          下一页
        </button>
      </div>
    </div>
  </section>
</template>
