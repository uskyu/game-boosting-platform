/**
 * Wallet store using Pinia.
 * Manages wallet balance, transaction history, withdrawal requests
 * and admin-side wallet operations (review / mark-paid / adjust / assign).
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/utils/api'

// ── Display metadata shared by wallet views ──

export const WITHDRAWAL_CHANNELS = [
  { value: 'ALIPAY', label: '支付宝' },
  { value: 'WECHAT', label: '微信' },
  { value: 'BANK', label: '银行卡' },
]

export const TRANSACTION_TYPE_META = {
  ORDER_INCOME: { label: '订单收入', direction: 'in' },
  ADMIN_ADJUST: { label: '平台调账', direction: 'auto' },
  WITHDRAWAL_FREEZE: { label: '提现冻结', direction: 'out' },
  WITHDRAWAL_REFUND: { label: '提现退回', direction: 'in' },
  WITHDRAWAL_PAID: { label: '提现打款', direction: 'out' },
  RECHARGE: { label: '充值入账', direction: 'in' },
  // 人工把已到账充值改判失败时的冲正，可用余额扣回
  RECHARGE_REVERSAL: { label: '充值冲正', direction: 'out' },
  // 下面这些是既有流水类型，此前漏登记会直接显示英文枚举值
  ESCROW_HOLD: { label: '发单托管', direction: 'out' },
  ESCROW_RELEASE: { label: '托管退回', direction: 'in' },
  ORDER_PAYMENT: { label: '订单打款', direction: 'out' },
  DEPOSIT_HOLD: { label: '赔偿金冻结', direction: 'out' },
  DEPOSIT_RELEASE: { label: '赔偿金解冻', direction: 'in' },
  COMPENSATION_DEDUCT: { label: '炸单赔偿', direction: 'out' },
  DEPOSIT_TRANSFER_IN: { label: '转入保证金', direction: 'out' },
  DEPOSIT_TRANSFER_OUT: { label: '保证金转回', direction: 'in' },
  CANCEL_COMPENSATION_IN: { label: '取消赔偿入账', direction: 'in' },
  CANCEL_COMPENSATION_DEDUCT: { label: '取消赔偿扣除', direction: 'out' },
  AFTERSALE_DEDUCTION: { label: '售后扣划', direction: 'out' },
  AFTERSALE_REFUND: { label: '售后收款', direction: 'in' },
  AFTERSALE_DEPOSIT_DEDUCTION: { label: '售后保证金抵扣', direction: 'out' },
}

// 充值（支付）记录状态：与后端 RechargeStatus 一一对应，包括 058 新增的 FAILED
export const RECHARGE_STATUS_META = {
  PENDING: { label: '待支付', tagClass: '!bg-warning-soft !text-warning' },
  SUCCESS: { label: '已到账', tagClass: '!bg-success-soft !text-success' },
  CLOSED: { label: '已关闭', tagClass: '!bg-surface-3 !text-ink-2' },
  FAILED: { label: '失败', tagClass: '!bg-danger-soft !text-danger' },
}

export const RECHARGE_STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '待支付' },
  { value: 'SUCCESS', label: '已到账' },
  { value: 'FAILED', label: '失败' },
  { value: 'CLOSED', label: '已关闭' },
]

export function getRechargeStatusMeta(status) {
  return (
    RECHARGE_STATUS_META[status] || {
      label: status || '未知状态',
      tagClass: '!bg-surface-3 !text-ink-2',
    }
  )
}

export const WITHDRAWAL_STATUS_META = {
  PENDING: {
    label: '待处理',
    tagClass: '!bg-warning-soft !text-warning',
  },
  APPROVED: {
    label: '待打款',
    tagClass: '!bg-info-soft !text-info',
  },
  PAID: {
    label: '已打款',
    tagClass: '!bg-success-soft !text-success',
  },
  REJECTED: {
    label: '已驳回',
    tagClass: '!bg-danger-soft !text-danger',
  },
}

export const WITHDRAWAL_STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '待处理' },
  { value: 'APPROVED', label: '待打款' },
  { value: 'PAID', label: '已打款' },
  { value: 'REJECTED', label: '已驳回' },
]

export function getTransactionTypeLabel(type) {
  return TRANSACTION_TYPE_META[type]?.label || type || '未知类型'
}

export function getWithdrawalStatusMeta(status) {
  return WITHDRAWAL_STATUS_META[status] || {
    label: status || '未知状态',
    tagClass: '!bg-surface-3 !text-ink-2',
  }
}

export function getWithdrawalStatusLabel(status) {
  return getWithdrawalStatusMeta(status).label
}

export function getWithdrawalStatusTagClass(status) {
  return getWithdrawalStatusMeta(status).tagClass
}

export function getChannelLabel(channel) {
  return WITHDRAWAL_CHANNELS.find((item) => item.value === channel)?.label || channel || '-'
}

// ── Helpers ──

function toNumber(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function buildPagination(data, fallbackPageSize) {
  const total = toNumber(data?.total, 0)
  const pageSize = Math.max(1, toNumber(data?.page_size, fallbackPageSize))
  return {
    page: Math.max(1, toNumber(data?.page, 1)),
    pageSize,
    total,
    pages: Math.max(1, Math.ceil(total / pageSize)),
  }
}

// 保证金阶梯：数值可能以字符串返回，统一转 Number 便于前端比较与高亮。
// 注意 `settle_hours` 的 **null 必须原样保留**：None = 该档留空、按订单到账
// 时效结算；若用 toNumber(..., 0) 兜底会变成 0，而 0 的语义是「立即结算」，
// 页面就会把「按订单时效」错显成「立即结算」（老板 2026-10-18 报的 bug）。
function normalizeDepositTier(tier) {
  return {
    id: tier?.id,
    threshold: toNumber(tier?.threshold, 0),
    wait_seconds: toNumber(tier?.wait_seconds, 0),
    exempt_compensation: Boolean(tier?.exempt_compensation),
    settle_hours: tier?.settle_hours == null ? null : toNumber(tier.settle_hours, 0),
    daily_accept_limit: tier?.daily_accept_limit == null ? null : toNumber(tier.daily_accept_limit, 0),
    enabled: tier?.enabled !== false,
    updated_at: tier?.updated_at,
  }
}

function normalizeDepositOverview(data) {
  const source = data || {}
  return {
    enabled: Boolean(source.enabled),
    deposit_balance: toNumber(source.deposit_balance, 0),
    available_balance: toNumber(source.available_balance, 0),
    current_threshold: source.current_threshold != null ? toNumber(source.current_threshold, 0) : null,
    wait_seconds: toNumber(source.wait_seconds, 0),
    exempt_compensation: Boolean(source.exempt_compensation),
    // 同上：null 必须保留，不能兜底成 0（0 = 立即结算）
    settle_hours: source.settle_hours == null ? null : toNumber(source.settle_hours, 0),
    daily_accept_limit: source.daily_accept_limit == null ? null : toNumber(source.daily_accept_limit, 0),
    daily_accepted_count: toNumber(source.daily_accepted_count, 0),
    daily_limit_reset_at: source.daily_limit_reset_at || null,
    can_return: Boolean(source.can_return),
    return_block_reason: source.return_block_reason || '',
    // 冷却天数（后台可配，默认 7）：转回说明文案要用它
    return_cooldown_days: toNumber(source.return_cooldown_days, 7),
    tiers: Array.isArray(source.tiers) ? source.tiers.map(normalizeDepositTier) : [],
  }
}

function normalizeDepositSettings(data) {
  const source = data || {}
  return {
    enabled: Boolean(source.enabled),
    return_cooldown_days: toNumber(source.return_cooldown_days, 0),
    default_compensation: toNumber(source.default_compensation, 0),
    settlement_mode: source.settlement_mode || 'AFTER_DELIVERY',
    global_booster_quota: toNumber(source.global_booster_quota, 5),
    updated_at: source.updated_at,
    tiers: Array.isArray(source.tiers) ? source.tiers.map(normalizeDepositTier) : [],
  }
}

export const useWalletStore = defineStore('wallet', () => {
  // State
  const wallet = ref(null)
  const walletLoading = ref(false)
  const transactions = ref([])
  const transactionsLoading = ref(false)
  const transactionsPagination = ref({ page: 1, pageSize: 10, total: 0, pages: 1 })
  const myWithdrawals = ref([])
  const myWithdrawalsLoading = ref(false)
  const myWithdrawalsPagination = ref({ page: 1, pageSize: 10, total: 0, pages: 1 })
  const adminWithdrawals = ref([])
  const adminWithdrawalsLoading = ref(false)
  const adminWithdrawalsPagination = ref({ page: 1, pageSize: 99, total: 0, pages: 1 })
  const rechargeConfig = ref({ enabled: false, pay_methods: [], min_amount: '1.00' })
  const rechargeConfigLoading = ref(false)
  const myRecharges = ref([])
  const myRechargesLoading = ref(false)
  const myRechargesPagination = ref({ page: 1, pageSize: 10, total: 0, pages: 1 })
  const paymentSettings = ref(null)
  const paymentSettingsLoading = ref(false)
  // 后台「支付记录」：全平台充值单 + 顶部统计
  const adminRecharges = ref([])
  const adminRechargesLoading = ref(false)
  const adminRechargesPagination = ref({ page: 1, pageSize: 20, total: 0, pages: 1 })
  const adminRechargeStats = ref({
    total: 0,
    success: 0,
    pending: 0,
    failed: 0,
    success_amount: 0,
  })
  const depositOverview = ref(null)
  const depositOverviewLoading = ref(false)
  const depositSubmitting = ref(false)
  const depositSettings = ref(null)
  const depositSettingsLoading = ref(false)
  const submitting = ref(false)
  const error = ref(null)
  // 提现机会（刷新规则）：available=false 时禁用提交，next_refresh_at 为下次恢复时间
  const quota = ref(null)

  // Actions
  async function fetchWallet() {
    walletLoading.value = true
    error.value = null

    try {
      const response = await api.get('/wallet')
      const data = response.data || {}
      // 数值可能以字符串返回，统一转 Number 容错
      wallet.value = {
        available_balance: toNumber(data.available_balance),
        frozen_balance: toNumber(data.frozen_balance),
        deposit_balance: toNumber(data.deposit_balance),
        total_income: toNumber(data.total_income),
        total_withdrawn: toNumber(data.total_withdrawn),
      }
      return { success: true, data: wallet.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      walletLoading.value = false
    }
  }

  async function fetchTransactions(options = {}) {
    transactionsLoading.value = true
    error.value = null

    try {
      const response = await api.get('/wallet/transactions', {
        params: {
          page: options.page || transactionsPagination.value.page,
          page_size: options.pageSize || transactionsPagination.value.pageSize,
        },
      })

      transactions.value = response.data?.items || []
      transactionsPagination.value = buildPagination(response.data, transactionsPagination.value.pageSize)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      transactionsLoading.value = false
    }
  }

  async function createWithdrawal(payload) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post('/withdrawals', payload)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // 上传提现收款二维码：POST /withdrawals/qrcode（multipart file）→ {url,name,size,content_type}
  async function uploadWithdrawalQrcode(file) {
    submitting.value = true
    error.value = null

    try {
      const form = new FormData()
      form.append('file', file)
      const response = await api.post('/withdrawals/qrcode', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function fetchMyWithdrawals(options = {}) {
    myWithdrawalsLoading.value = true
    error.value = null

    try {
      const response = await api.get('/withdrawals/mine', {
        params: {
          page: options.page || myWithdrawalsPagination.value.page,
          page_size: options.pageSize || myWithdrawalsPagination.value.pageSize,
        },
      })

      myWithdrawals.value = response.data?.items || []
      myWithdrawalsPagination.value = buildPagination(response.data, myWithdrawalsPagination.value.pageSize)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      myWithdrawalsLoading.value = false
    }
  }

  async function fetchAdminWithdrawals(options = {}) {
    adminWithdrawalsLoading.value = true
    error.value = null

    try {
      const response = await api.get('/admin/withdrawals', {
        params: {
          status: options.status || undefined,
          channel: options.channel || undefined,
          page: options.page || adminWithdrawalsPagination.value.page,
          page_size: options.pageSize || adminWithdrawalsPagination.value.pageSize,
        },
      })

      adminWithdrawals.value = response.data?.items || []
      adminWithdrawalsPagination.value = buildPagination(response.data, adminWithdrawalsPagination.value.pageSize)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      adminWithdrawalsLoading.value = false
    }
  }

  // GET /admin/withdrawal-payout/selectable-withdrawals：
  // 当前筛选（status/channel）下「可加入批次」的提现汇总，供「全选当前筛选」预览与一键勾选。
  // 命中数超过单批上限时后端 items 为空、exceeded=true，前端据此禁用按钮。
  async function fetchSelectableWithdrawals(options = {}) {
    try {
      const response = await api.get('/admin/withdrawal-payout/selectable-withdrawals', {
        params: {
          status: options.status || undefined,
          channel: options.channel || undefined,
        },
      })
      return { success: true, data: response.data }
    } catch (err) {
      return { success: false, error: err.message }
    }
  }

  async function reviewWithdrawal(withdrawalId, action, reason = '') {
    submitting.value = true
    error.value = null

    try {
      const body = { action }
      if (reason) {
        body.reason = reason
      }
      const response = await api.post(`/admin/withdrawals/${withdrawalId}/review`, body)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function markPaid(withdrawalId, paymentReference) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post(`/admin/withdrawals/${withdrawalId}/mark-paid`, {
        payment_reference: paymentReference,
      })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function adjustWallet(userId, amount, reason) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post(`/admin/wallets/${userId}/adjust`, {
        amount: toNumber(amount),
        reason,
      })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function assignOrder(orderId, boosterId, reason = '') {
    submitting.value = true
    error.value = null

    try {
      const body = { booster_id: toNumber(boosterId) }
      if (reason) {
        body.reason = reason
      }
      const response = await api.put(`/admin/orders/${orderId}/assign`, body)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // ── 易支付充值（用户侧）──

  async function fetchRechargeConfig() {
    rechargeConfigLoading.value = true
    error.value = null

    try {
      const response = await api.get('/wallet/recharge/config')
      const data = response.data || {}
      rechargeConfig.value = {
        enabled: Boolean(data.enabled),
        pay_methods: Array.isArray(data.pay_methods) ? data.pay_methods : [],
        min_amount: data.min_amount != null ? String(data.min_amount) : '1.00',
      }
      return { success: true, data: rechargeConfig.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      rechargeConfigLoading.value = false
    }
  }

  async function createRecharge(amount, paymentMethod) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post('/wallet/recharge', {
        amount: toNumber(amount),
        payment_method: paymentMethod,
      })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function fetchMyRecharges(options = {}) {
    myRechargesLoading.value = true
    error.value = null

    try {
      const response = await api.get('/wallet/recharge/mine', {
        params: {
          page: options.page || myRechargesPagination.value.page,
          page_size: options.pageSize || myRechargesPagination.value.pageSize,
        },
      })

      myRecharges.value = response.data?.items || []
      myRechargesPagination.value = buildPagination(response.data, myRechargesPagination.value.pageSize)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      myRechargesLoading.value = false
    }
  }

  // ── 易支付充值（管理员设置）──

  async function fetchPaymentSettings() {
    paymentSettingsLoading.value = true
    error.value = null

    try {
      const response = await api.get('/admin/payment/settings')
      paymentSettings.value = response.data || null
      return { success: true, data: paymentSettings.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      paymentSettingsLoading.value = false
    }
  }

  async function updatePaymentSettings(payload) {
    paymentSettingsLoading.value = true
    error.value = null

    try {
      const response = await api.put('/admin/payment/settings', payload)
      paymentSettings.value = response.data || paymentSettings.value
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      paymentSettingsLoading.value = false
    }
  }

  // ── 后台「支付记录」──

  // GET /admin/payment/recharges：全平台充值单（分页 + 状态筛选 + 关键词搜索）
  async function fetchAdminRecharges(options = {}) {
    adminRechargesLoading.value = true
    error.value = null

    try {
      const params = {
        page: options.page || adminRechargesPagination.value.page,
        page_size: options.pageSize || adminRechargesPagination.value.pageSize,
      }
      if (options.status) params.status = options.status
      if (options.q) params.q = options.q

      const response = await api.get('/admin/payment/recharges', { params })
      const data = response.data || {}
      adminRecharges.value = data.items || []
      adminRechargesPagination.value = buildPagination(
        data,
        adminRechargesPagination.value.pageSize
      )
      if (data.stats) {
        adminRechargeStats.value = {
          total: toNumber(data.stats.total, 0),
          success: toNumber(data.stats.success, 0),
          pending: toNumber(data.stats.pending, 0),
          failed: toNumber(data.stats.failed, 0),
          success_amount: toNumber(data.stats.success_amount, 0),
        }
      }
      return { success: true, data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      adminRechargesLoading.value = false
    }
  }

  // POST /admin/payment/recharges/{id}/status：人工改判成功 / 失败
  async function setAdminRechargeStatus(id, status, reason) {
    submitting.value = true
    error.value = null

    try {
      const payload = { status }
      if (reason != null && String(reason).trim()) payload.reason = String(reason).trim()
      const response = await api.post(`/admin/payment/recharges/${id}/status`, payload)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // ── 保证金（用户侧）──
  // GET /wallet/deposit：保证金概览 + 阶梯权益（管理员调用返回 403）
  async function fetchDepositOverview() {
    depositOverviewLoading.value = true
    error.value = null

    try {
      const response = await api.get('/wallet/deposit')
      depositOverview.value = normalizeDepositOverview(response.data)
      return { success: true, data: depositOverview.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      depositOverviewLoading.value = false
    }
  }

  // POST /wallet/deposit/in：从可用余额转入保证金
  async function transferToDeposit(amount) {
    depositSubmitting.value = true
    error.value = null

    try {
      const response = await api.post('/wallet/deposit/in', { amount: toNumber(amount) })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      depositSubmitting.value = false
    }
  }

  // POST /wallet/deposit/out：保证金转回可用余额
  async function transferFromDeposit(amount) {
    depositSubmitting.value = true
    error.value = null

    try {
      const response = await api.post('/wallet/deposit/out', { amount: toNumber(amount) })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      depositSubmitting.value = false
    }
  }

  // ── 保证金（管理员设置）──

  async function fetchDepositSettings() {
    depositSettingsLoading.value = true
    error.value = null

    try {
      const response = await api.get('/admin/deposit/settings')
      depositSettings.value = normalizeDepositSettings(response.data)
      return { success: true, data: depositSettings.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      depositSettingsLoading.value = false
    }
  }

  async function updateDepositSettings(payload) {
    depositSettingsLoading.value = true
    error.value = null

    try {
      const response = await api.put('/admin/deposit/settings', payload)
      depositSettings.value = normalizeDepositSettings(response.data)
      return { success: true, data: depositSettings.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      depositSettingsLoading.value = false
    }
  }

  function clearError() {
    error.value = null
  }

  // GET /withdrawals/quota：提现机会与下次恢复时间（服务端按刷新规则计算）
  async function fetchQuota() {
    try {
      const response = await api.get('/withdrawals/quota')
      quota.value = response.data || null
      return { success: true, data: quota.value }
    } catch (err) {
      // 拉不到机会不阻塞钱包页其余数据，仅返回错误供调用方忽略
      return { success: false, error: err.message }
    }
  }

  return {
    // State
    wallet,
    walletLoading,
    transactions,
    transactionsLoading,
    transactionsPagination,
    myWithdrawals,
    myWithdrawalsLoading,
    myWithdrawalsPagination,
    adminWithdrawals,
    adminWithdrawalsLoading,
    adminWithdrawalsPagination,
    rechargeConfig,
    rechargeConfigLoading,
    myRecharges,
    myRechargesLoading,
    myRechargesPagination,
    paymentSettings,
    paymentSettingsLoading,
    adminRecharges,
    adminRechargesLoading,
    adminRechargesPagination,
    adminRechargeStats,
    depositOverview,
    depositOverviewLoading,
    depositSubmitting,
    depositSettings,
    depositSettingsLoading,
    submitting,
    error,
    quota,
    // Actions
    fetchWallet,
    fetchTransactions,
    createWithdrawal,
    uploadWithdrawalQrcode,
    fetchMyWithdrawals,
    fetchAdminWithdrawals,
    fetchSelectableWithdrawals,
    reviewWithdrawal,
    markPaid,
    adjustWallet,
    assignOrder,
    fetchRechargeConfig,
    createRecharge,
    fetchMyRecharges,
    fetchPaymentSettings,
    updatePaymentSettings,
    fetchAdminRecharges,
    setAdminRechargeStatus,
    fetchDepositOverview,
    transferToDeposit,
    transferFromDeposit,
    fetchDepositSettings,
    updateDepositSettings,
    fetchQuota,
    clearError,
  }
})
