<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAdminUsersStore } from '@/stores/adminUsers'
import { formatDateTime, formatPrice } from '@/utils/display'

const router = useRouter()
const auth = useAuthStore()
const store = useAdminUsersStore()

// ── 筛选（角色两态：管理员 / 打手）──

const filters = reactive({ query: '', role: '', isActive: '', restriction: '' })
const roleFilterOptions = [
  { value: '', label: '全部角色' },
  { value: 'ADMIN', label: '管理员' },
  { value: 'BOOSTER', label: '打手' },
]
const statusFilterOptions = [
  { value: '', label: '全部状态' },
  { value: true, label: '启用' },
  { value: false, label: '禁用' },
]
const restrictionFilterOptions = [
  { value: '', label: '全部限制' },
  { value: 'no_publish', label: '禁发单' },
  { value: 'no_accept', label: '禁接单' },
]

// 限制状态本地过滤（后端 list 暂无该筛选）
const visibleUsers = computed(() => {
  if (filters.restriction === 'no_publish') return store.users.filter((u) => u.can_publish === false)
  if (filters.restriction === 'no_accept') return store.users.filter((u) => u.can_accept === false)
  return store.users
})

// ── 通知 flash ──

const notice = ref({ type: '', text: '' })
let noticeTimer
function flash(type, text) {
  notice.value = { type, text }
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value.text = '' }, 3500)
}

function load(page = 1) {
  return store.fetchUsers({
    page,
    query: filters.query.trim(),
    role: filters.role,
    isActive: filters.isActive,
  })
}
watch(() => [filters.role, filters.isActive], () => load(1))

// ── 角色展示（ADMIN=管理员，其余一律视为打手）──

function roleLabel(role) {
  return role === 'ADMIN' ? '管理员' : '打手'
}
function roleTagClass(role) {
  return role === 'ADMIN' ? 'tag !bg-primary-soft !text-primary' : 'tag'
}
function avatarText(user) {
  return (user.username || '?').slice(0, 1).toUpperCase()
}

// ── 编辑弹窗（含角色，USER 回显为 BOOSTER）──

const editModal = ref(null)
const editRoleOptions = [
  { value: 'ADMIN', label: '管理员' },
  { value: 'BOOSTER', label: '打手' },
]

async function openEdit(user) {
  const result = await store.getUser(user.id)
  if (!result.success) flash('error', result.error)
  const detail = result.success ? result.data : user
  editModal.value = {
    id: detail.id,
    username: detail.username || '',
    phone: detail.phone || '',
    bio: detail.bio || '',
    is_verified: !!detail.is_verified,
    booster_quota: detail.booster_quota ?? 0,
    role: detail.role === 'ADMIN' ? 'ADMIN' : 'BOOSTER',
    originalRole: detail.role,
    error: '',
    submitting: false,
  }
}

async function saveEdit() {
  const state = editModal.value
  if (!state || state.submitting) return
  state.error = ''
  if (!state.username.trim()) {
    state.error = '请填写用户名'
    return
  }
  state.submitting = true
  const payload = {
    username: state.username.trim(),
    phone: state.phone.trim() || null,
    bio: state.bio.trim() || null,
    is_verified: state.is_verified,
    booster_quota: Number(state.booster_quota) || 0,
  }
  // 仅当用户改过角色、或原角色本就非 ADMIN（如 USER→BOOSTER）时提交 role
  if (state.role !== state.originalRole || state.originalRole !== 'ADMIN') payload.role = state.role
  const result = await store.updateUser(state.id, payload)
  state.submitting = false
  if (result.success) {
    editModal.value = null
    flash('success', '用户资料已更新')
    await load(store.pagination.page)
  } else {
    state.error = result.error
  }
}

// ── 重置密码（生成强密码）──

function strongPassword() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  let value = 'Aa1'
  for (let i = 3; i < 14; i++) value += chars[Math.floor(Math.random() * chars.length)]
  return value
}

async function resetPassword(user) {
  const password = strongPassword()
  const result = await store.resetPassword(user.id, password)
  if (result.success) flash('success', `密码已重置为：${password}`)
  else flash('error', result.error)
}

// ── 启用 / 禁用 ──

function isSelf(user) {
  return user.id === auth.user?.id
}

async function toggleStatus(user) {
  const result = await store.setStatus(user.id, !user.is_active)
  if (result.success) {
    flash('success', '状态已更新')
    await load(store.pagination.page)
  } else {
    flash('error', result.error)
  }
}

// ── 禁发单 / 禁接单 ──

async function togglePublish(user) {
  const next = !(user.can_publish ?? true)
  const result = await store.setRestrictions(user.id, { can_publish: next })
  if (result.success) {
    flash('success', next ? '已解除禁发单' : '已禁发单')
    await load(store.pagination.page)
  } else {
    flash('error', result.error)
  }
}

async function toggleAccept(user) {
  const next = !(user.can_accept ?? true)
  const result = await store.setRestrictions(user.id, { can_accept: next })
  if (result.success) {
    flash('success', next ? '已解除禁接单' : '已禁接单')
    await load(store.pagination.page)
  } else {
    flash('error', result.error)
  }
}

// ── 调整余额（正充值 / 负扣减）──

const balanceModal = ref(null)

function openBalance(user) {
  balanceModal.value = { user, amount: '', reason: '', error: '', submitting: false }
}

async function saveBalance() {
  const state = balanceModal.value
  if (!state || state.submitting) return
  state.error = ''
  const amount = Number(state.amount)
  if (state.amount === '' || !Number.isFinite(amount) || amount === 0) {
    state.error = '金额不能为 0（正数充值、负数扣减）'
    return
  }
  if (!state.reason.trim()) {
    state.error = '请填写调整原因'
    return
  }
  state.submitting = true
  const result = await store.adjustBalance(state.user.id, amount, state.reason.trim())
  state.submitting = false
  if (result.success) {
    balanceModal.value = null
    flash('success', `余额已调整（${amount > 0 ? '+' : ''}${formatPrice(amount)}）`)
    await load(store.pagination.page)
  } else {
    state.error = result.error
  }
}

// ── 余额明细弹窗 ──

const transactionsModal = ref(null) // { user, wallet, items, page, pages, total, loading, error }

async function openTransactions(user) {
  transactionsModal.value = { user, wallet: user.wallet || {}, items: [], page: 1, pages: 0, total: 0, loading: true, error: '' }
  // 拉最新详情，钱包汇总以详情为准（失败则回退列表数据）
  const detail = await store.getUser(user.id)
  if (transactionsModal.value && detail.success) transactionsModal.value.wallet = detail.data.wallet || {}
  await loadTransactions(1)
}

async function loadTransactions(page = 1) {
  const state = transactionsModal.value
  if (!state) return
  state.loading = true
  state.error = ''
  const result = await store.fetchUserTransactions(state.user.id, page)
  const current = transactionsModal.value
  if (!current || current !== state) return
  if (result.success) {
    const data = result.data || {}
    current.items = data.items || []
    current.page = data.page || 1
    current.pages = data.pages || 0
    current.total = data.total || 0
  } else {
    current.error = result.error
  }
  current.loading = false
}

const TX_TYPE_LABELS = {
  ORDER_INCOME: '订单收入 (+)',
  ADMIN_ADJUST: '管理员调账 (±)',
  WITHDRAWAL_FREEZE: '提现冻结 (-)',
  WITHDRAWAL_REFUND: '提现驳回回补 (+)',
  WITHDRAWAL_PAID: '提现打款 (-)',
}
function txTypeLabel(type) {
  return TX_TYPE_LABELS[type] || type || '-'
}
function txAmountLabel(amount) {
  const value = Number(amount ?? 0)
  return value > 0 ? `+${formatPrice(value)}` : formatPrice(value)
}
function txAmountClass(amount) {
  const value = Number(amount ?? 0)
  return value > 0 ? 'text-success' : value < 0 ? 'text-danger' : 'text-ink-1'
}

function goOrder(orderId) {
  router.push(`/admin/dispatch/${orderId}`)
}

onMounted(load)
</script>

<template>
  <section v-if="auth.isAdmin" class="surface-card p-4 sm:p-6">
    <!-- 顶部工具卡：搜索 + 角色 / 状态筛选 -->
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center">
      <input
        v-model="filters.query"
        type="text"
        class="input min-h-[44px] flex-1"
        placeholder="搜索用户名或邮箱"
        @keyup.enter="load(1)"
      />
      <select v-model="filters.role" class="input min-h-[44px] sm:w-36" aria-label="角色筛选">
        <option v-for="option in roleFilterOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
      <select v-model="filters.isActive" class="input min-h-[44px] sm:w-32" aria-label="状态筛选">
        <option v-for="option in statusFilterOptions" :key="String(option.value)" :value="option.value">{{ option.label }}</option>
      </select>
      <select v-model="filters.restriction" class="input min-h-[44px] sm:w-32" aria-label="限制筛选">
        <option v-for="option in restrictionFilterOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
      <button type="button" class="btn-primary min-h-[44px] !px-6" :disabled="store.loading" @click="load(1)">
        {{ store.loading ? '搜索中...' : '搜索' }}
      </button>
    </div>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">{{ notice.text }}</div>

    <!-- 加载骨架 -->
    <div v-if="store.loading" class="mt-5 space-y-2" aria-busy="true">
      <div v-for="n in 5" :key="`user-skeleton-${n}`" class="skeleton h-16 !rounded-tile"></div>
    </div>

    <template v-else>
      <!-- 空态 -->
      <div v-if="!visibleUsers.length" class="empty-state mt-6">
        <div class="empty-state__icon" aria-hidden="true">👥</div>
        <h3 class="empty-state__title">暂无用户</h3>
        <p class="empty-state__copy">换个关键词或筛选条件试试。</p>
      </div>

      <template v-else>
        <!-- 桌面（sm+）：数据表 -->
        <div class="mt-5 hidden overflow-x-auto sm:block">
          <table class="data-table hidden !min-w-[920px] sm:table">
            <thead>
              <tr>
                <th>#ID</th>
                <th>用户</th>
                <th>角色</th>
                <th>状态</th>
                <th class="text-right">可用余额</th>
                <th class="text-right">累计收入</th>
                <th class="text-right">累计提现</th>
                <th>注册时间</th>
                <th class="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in visibleUsers" :key="user.id">
                <td class="tabular-nums text-ink-3">#{{ user.id }}</td>
                <td>
                  <div class="flex items-center gap-3">
                    <span class="user-avatar">{{ avatarText(user) }}</span>
                    <div class="min-w-0">
                      <p class="truncate font-medium text-ink-1">{{ user.username }}</p>
                      <p class="truncate text-xs text-ink-3">{{ user.email }}</p>
                      <div v-if="user.can_publish === false || user.can_accept === false" class="mt-1 flex flex-wrap gap-1">
                        <span v-if="user.can_publish === false" class="badge-cancelled">禁发单</span>
                        <span v-if="user.can_accept === false" class="badge-cancelled">禁接单</span>
                      </div>
                    </div>
                  </div>
                </td>
                <td><span :class="roleTagClass(user.role)">{{ roleLabel(user.role) }}</span></td>
                <td><span :class="user.is_active ? 'badge-approved' : 'badge-cancelled'">{{ user.is_active ? '启用' : '禁用' }}</span></td>
                <td class="text-right"><span class="font-semibold tabular-nums text-price">{{ formatPrice(user.wallet?.available) }}</span></td>
                <td class="text-right tabular-nums text-ink-2">{{ formatPrice(user.wallet?.total_income) }}</td>
                <td class="text-right tabular-nums text-ink-2">{{ formatPrice(user.wallet?.total_withdrawn) }}</td>
                <td class="whitespace-nowrap text-ink-3">{{ formatDateTime(user.created_at) }}</td>
                <td>
                  <div class="flex flex-wrap justify-end gap-1.5">
                    <button type="button" class="btn-ghost !px-3 !py-1.5" @click="openEdit(user)">编辑</button>
                    <button type="button" class="btn-secondary !px-3 !py-1.5" @click="openTransactions(user)">明细</button>
                    <button type="button" class="btn-ghost !px-3 !py-1.5" @click="resetPassword(user)">重置密码</button>
                    <button type="button" class="btn-ghost !px-3 !py-1.5" @click="toggleStatus(user)">{{ user.is_active ? '禁用' : '启用' }}</button>
                    <button
                      type="button"
                      class="btn-ghost !px-3 !py-1.5"
                      :disabled="isSelf(user)"
                      :title="isSelf(user) ? '不能给自己设置' : ''"
                      @click="togglePublish(user)"
                    >{{ (user.can_publish ?? true) ? '禁发单' : '解禁发单' }}</button>
                    <button
                      type="button"
                      class="btn-ghost !px-3 !py-1.5"
                      :disabled="isSelf(user)"
                      :title="isSelf(user) ? '不能给自己设置' : ''"
                      @click="toggleAccept(user)"
                    >{{ (user.can_accept ?? true) ? '禁接单' : '解除禁接单' }}</button>
                    <button type="button" class="btn-ghost !px-3 !py-1.5" @click="openBalance(user)">调余额</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 移动端（<sm）：卡片列表 -->
        <div class="mt-5 space-y-3 sm:hidden">
          <article v-for="user in visibleUsers" :key="user.id" class="rounded-tile border border-line-1 bg-surface-2 p-4">
            <div class="flex items-center gap-3">
              <span class="user-avatar">{{ avatarText(user) }}</span>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <p class="truncate font-semibold text-ink-1">{{ user.username }}</p>
                  <span :class="roleTagClass(user.role)">{{ roleLabel(user.role) }}</span>
                  <span :class="user.is_active ? 'badge-approved' : 'badge-cancelled'">{{ user.is_active ? '启用' : '禁用' }}</span>
                  <span v-if="user.can_publish === false" class="badge-cancelled">禁发单</span>
                  <span v-if="user.can_accept === false" class="badge-cancelled">禁接单</span>
                </div>
                <p class="mt-0.5 truncate text-xs text-ink-3">#{{ user.id }} · {{ user.email }}</p>
              </div>
            </div>
            <p class="mt-3 text-xl font-semibold tabular-nums text-price">{{ formatPrice(user.wallet?.available) }}</p>
            <p class="mt-1 text-xs tabular-nums text-ink-3">
              收入 {{ formatPrice(user.wallet?.total_income) }} · 提现 {{ formatPrice(user.wallet?.total_withdrawn) }} · 注册于 {{ formatDateTime(user.created_at) }}
            </p>
            <div class="mt-3 flex flex-wrap gap-2">
              <button type="button" class="btn-secondary min-h-[44px] !px-4 !py-2" @click="openTransactions(user)">明细</button>
              <button type="button" class="btn-ghost min-h-[44px] !px-4 !py-2" @click="openEdit(user)">编辑</button>
              <button type="button" class="btn-ghost min-h-[44px] !px-4 !py-2" @click="resetPassword(user)">重置密码</button>
              <button type="button" class="btn-ghost min-h-[44px] !px-4 !py-2" @click="toggleStatus(user)">{{ user.is_active ? '禁用' : '启用' }}</button>
              <button
                type="button"
                class="btn-ghost min-h-[44px] !px-4 !py-2"
                :disabled="isSelf(user)"
                :title="isSelf(user) ? '不能给自己设置' : ''"
                @click="togglePublish(user)"
              >{{ (user.can_publish ?? true) ? '禁发单' : '解禁发单' }}</button>
              <button
                type="button"
                class="btn-ghost min-h-[44px] !px-4 !py-2"
                :disabled="isSelf(user)"
                :title="isSelf(user) ? '不能给自己设置' : ''"
                @click="toggleAccept(user)"
              >{{ (user.can_accept ?? true) ? '禁接单' : '解除禁接单' }}</button>
              <button type="button" class="btn-ghost min-h-[44px] !px-4 !py-2" @click="openBalance(user)">调余额</button>
            </div>
          </article>
        </div>

        <!-- 分页 -->
        <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-sm tabular-nums text-ink-2">
            第 {{ store.pagination.page }} / {{ store.pagination.pages || 1 }} 页，共 {{ store.pagination.total }} 人
          </p>
          <div class="flex gap-2">
            <button
              type="button"
              class="btn-secondary min-h-[44px] !px-4"
              :disabled="store.pagination.page <= 1"
              @click="load(store.pagination.page - 1)"
            >
              上一页
            </button>
            <button
              type="button"
              class="btn-secondary min-h-[44px] !px-4"
              :disabled="store.pagination.page >= store.pagination.pages"
              @click="load(store.pagination.page + 1)"
            >
              下一页
            </button>
          </div>
        </div>
      </template>
    </template>

    <!-- 编辑用户弹窗 -->
    <div v-if="editModal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="editModal = null"></div>
      <div class="modal-card modal-sheet" role="dialog" aria-modal="true" aria-label="编辑用户">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-ink-1">编辑用户</h3>
          <p class="mt-2 text-sm text-ink-2">#{{ editModal.id }} · 当前角色：{{ roleLabel(editModal.originalRole) }}</p>

          <form class="mt-5 space-y-4" @submit.prevent="saveEdit">
            <div>
              <label class="label" for="edit-username">用户名</label>
              <input id="edit-username" v-model="editModal.username" type="text" class="input" maxlength="50" :disabled="editModal.submitting" />
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div>
                <label class="label" for="edit-phone">手机号</label>
                <input id="edit-phone" v-model="editModal.phone" type="tel" class="input" maxlength="20" :disabled="editModal.submitting" />
              </div>
              <div>
                <label class="label" for="edit-quota">打手名额</label>
                <input id="edit-quota" v-model.number="editModal.booster_quota" type="number" min="0" max="50" class="input" :disabled="editModal.submitting" />
              </div>
            </div>
            <div>
              <label class="label" for="edit-role">角色</label>
              <select id="edit-role" v-model="editModal.role" class="input" :disabled="editModal.submitting">
                <option v-for="option in editRoleOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
              <p class="mt-1 text-xs text-ink-3">打手即普通注册用户（含历史 USER 角色）；不能降级自己的管理员角色。</p>
            </div>
            <div>
              <label class="label" for="edit-bio">简介</label>
              <textarea id="edit-bio" v-model="editModal.bio" rows="3" class="input resize-none" maxlength="500" :disabled="editModal.submitting"></textarea>
            </div>
            <label class="flex min-h-[44px] items-center gap-2 text-sm text-ink-2">
              <input v-model="editModal.is_verified" type="checkbox" :disabled="editModal.submitting" /> 已认证
            </label>

            <div v-if="editModal.error" class="message-error">{{ editModal.error }}</div>

            <div class="flex justify-end gap-3 pt-2">
              <button type="button" class="btn-ghost !px-4 !py-2" :disabled="editModal.submitting" @click="editModal = null">取消</button>
              <button type="submit" class="btn-primary !px-5 !py-2" :disabled="editModal.submitting">
                {{ editModal.submitting ? '保存中...' : '保存' }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- 调整余额弹窗 -->
    <div v-if="balanceModal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="balanceModal = null"></div>
      <div class="modal-card modal-sheet" role="dialog" aria-modal="true" aria-label="调整余额">
        <div class="relative z-10">
          <h3 class="text-2xl font-semibold text-ink-1">调整余额</h3>
          <p class="mt-2 text-sm text-ink-2">
            {{ balanceModal.user.username }} · 当前可用
            <span class="font-semibold tabular-nums text-price">{{ formatPrice(balanceModal.user.wallet?.available) }}</span>
          </p>

          <form class="mt-5 space-y-4" @submit.prevent="saveBalance">
            <div>
              <label class="label" for="balance-amount">金额（正数充值 / 负数扣减）</label>
              <input id="balance-amount" v-model="balanceModal.amount" type="number" step="0.01" class="input" placeholder="例如 100 或 -50" :disabled="balanceModal.submitting" />
            </div>
            <div>
              <label class="label" for="balance-reason">原因</label>
              <input id="balance-reason" v-model="balanceModal.reason" type="text" class="input" maxlength="200" placeholder="请填写调整原因" :disabled="balanceModal.submitting" />
            </div>

            <div v-if="balanceModal.error" class="message-error">{{ balanceModal.error }}</div>

            <div class="flex justify-end gap-3 pt-2">
              <button type="button" class="btn-ghost !px-4 !py-2" :disabled="balanceModal.submitting" @click="balanceModal = null">取消</button>
              <button type="submit" class="btn-primary !px-5 !py-2" :disabled="balanceModal.submitting">
                {{ balanceModal.submitting ? '提交中...' : '提交' }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- 余额明细弹窗 -->
    <div v-if="transactionsModal" class="modal-scrim modal-scrim--sheet">
      <div class="absolute inset-0" aria-hidden="true" @click="transactionsModal = null"></div>
      <div class="modal-card modal-sheet" role="dialog" aria-modal="true" aria-label="余额明细">
        <div class="relative z-10">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-2xl font-semibold text-ink-1">余额明细</h3>
              <p class="mt-2 truncate text-sm text-ink-2">{{ transactionsModal.user.username }} · #{{ transactionsModal.user.id }}</p>
            </div>
            <button type="button" class="btn-ghost shrink-0 !px-4 !py-2" @click="transactionsModal = null">关闭</button>
          </div>

          <!-- 钱包汇总 -->
          <div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div class="info-tile">
              <p class="info-tile__label">可用余额</p>
              <p class="info-tile__value text-base font-semibold tabular-nums text-price">{{ formatPrice(transactionsModal.wallet?.available) }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">冻结</p>
              <p class="info-tile__value text-base tabular-nums">{{ formatPrice(transactionsModal.wallet?.frozen) }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">累计收入</p>
              <p class="info-tile__value text-base tabular-nums">{{ formatPrice(transactionsModal.wallet?.total_income) }}</p>
            </div>
            <div class="info-tile">
              <p class="info-tile__label">累计提现</p>
              <p class="info-tile__value text-base tabular-nums">{{ formatPrice(transactionsModal.wallet?.total_withdrawn) }}</p>
            </div>
          </div>

          <!-- 流水列表（时间倒序） -->
          <div v-if="transactionsModal.loading" class="mt-5 space-y-2" aria-busy="true">
            <div v-for="n in 4" :key="`tx-skeleton-${n}`" class="skeleton h-16 !rounded-tile"></div>
          </div>
          <div v-else-if="transactionsModal.error" class="message-error mt-5">{{ transactionsModal.error }}</div>
          <div v-else-if="!transactionsModal.items.length" class="empty-state mt-5">
            <div class="empty-state__icon" aria-hidden="true">🧾</div>
            <h4 class="empty-state__title">暂无流水记录</h4>
            <p class="empty-state__copy">该用户产生收支后，明细会出现在这里。</p>
          </div>
          <ul v-else class="mt-5 space-y-2">
            <li v-for="tx in transactionsModal.items" :key="tx.id" class="rounded-tile border border-line-1 bg-surface-2 p-3">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <p class="text-sm font-semibold text-ink-1">{{ txTypeLabel(tx.type) }}</p>
                    <button
                      v-if="tx.order_id"
                      type="button"
                      class="tag cursor-pointer tabular-nums transition-colors hover:border-primary hover:text-primary"
                      :aria-label="`查看订单 ${tx.order_id}`"
                      @click="goOrder(tx.order_id)"
                    >
                      #{{ tx.order_id }}
                    </button>
                  </div>
                  <p v-if="tx.remark" class="mt-1 break-words text-xs text-ink-3">{{ tx.remark }}</p>
                  <p class="mt-1 text-xs text-ink-3">{{ formatDateTime(tx.created_at) }}</p>
                </div>
                <div class="shrink-0 text-right">
                  <p class="text-sm font-semibold tabular-nums" :class="txAmountClass(tx.amount)">{{ txAmountLabel(tx.amount) }}</p>
                  <p class="mt-1 text-xs tabular-nums text-ink-3">余额 {{ formatPrice(tx.balance_after) }}</p>
                </div>
              </div>
            </li>
          </ul>

          <!-- 明细分页 -->
          <div v-if="!transactionsModal.loading && transactionsModal.pages > 1" class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p class="text-sm tabular-nums text-ink-2">
              第 {{ transactionsModal.page }} / {{ transactionsModal.pages }} 页，共 {{ transactionsModal.total }} 条
            </p>
            <div class="flex gap-2">
              <button
                type="button"
                class="btn-secondary !px-4 !py-2"
                :disabled="transactionsModal.page <= 1"
                @click="loadTransactions(transactionsModal.page - 1)"
              >
                上一页
              </button>
              <button
                type="button"
                class="btn-secondary !px-4 !py-2"
                :disabled="transactionsModal.page >= transactionsModal.pages"
                @click="loadTransactions(transactionsModal.page + 1)"
              >
                下一页
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 头像圈字：表格与移动卡片共用 */
.user-avatar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 9999px;
  border: 1px solid var(--line-1);
  background: var(--surface-3);
  color: var(--ink-2);
  font-size: 14px;
  font-weight: 600;
}
</style>
