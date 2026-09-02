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
  const adminWithdrawalsPagination = ref({ page: 1, pageSize: 20, total: 0, pages: 1 })
  const submitting = ref(false)
  const error = ref(null)

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

  function clearError() {
    error.value = null
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
    submitting,
    error,
    // Actions
    fetchWallet,
    fetchTransactions,
    createWithdrawal,
    uploadWithdrawalQrcode,
    fetchMyWithdrawals,
    fetchAdminWithdrawals,
    reviewWithdrawal,
    markPaid,
    adjustWallet,
    assignOrder,
    clearError,
  }
})
