/**
 * 售后（after-sales）store。
 * 覆盖：售后申请创建 / 订单售后查询 / 我的售后（打手或老板视角）/
 * 凭证图片上传 / 后台列表与审核。接口契约见后端 after_sales 端点。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import api from '@/utils/api'

export const AFTER_SALE_STATUS_META = {
  PENDING: { label: '待处理', tagClass: '!bg-warning-soft !text-warning' },
  APPROVED: { label: '已通过', tagClass: '!bg-success-soft !text-success' },
  REJECTED: { label: '已驳回', tagClass: '!bg-danger-soft !text-danger' },
  // 已取消：老板撤诉或后台「取消售后」的终态，不涉及扣款
  CLOSED: { label: '已取消', tagClass: '!bg-surface-3 !text-ink-2' },
}

export const AFTER_SALE_STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '待处理' },
  { value: 'APPROVED', label: '已通过' },
  { value: 'REJECTED', label: '已驳回' },
  { value: 'CLOSED', label: '已取消' },
]

export function getAfterSaleStatusLabel(status) {
  return AFTER_SALE_STATUS_META[status]?.label || status || '未知状态'
}

export function getAfterSaleStatusTagClass(status) {
  return AFTER_SALE_STATUS_META[status]?.tagClass || '!bg-surface-3 !text-ink-2'
}

// 打手有进行中售后时禁止提现：钱包页与提交失败兜底都读这个标记
export function isPendingAfterSale(item) {
  return item?.status === 'PENDING'
}

// 后端「可扣款不足」结构化 detail 的 code（见 after_sale_service.settle_approved_funds）
export const INSUFFICIENT_FUNDS_CODE = 'after_sale_insufficient_funds'

/**
 * 从 api 拦截器抛出的错误里解出「可扣款不足」的结构化信息。
 * 拦截器已把 detail 的 message 解成 err.message，完整结构留在 err.original；
 * 非该类错误返回 null（调用方继续走通用错误文案）。
 */
export function parseInsufficientFundsError(err) {
  const detail = err?.original?.response?.data?.detail
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null
  if (detail.code !== INSUFFICIENT_FUNDS_CODE) return null
  return {
    message: String(detail.message || err?.message || ''),
    required: String(detail.required ?? ''),
    available: String(detail.available ?? ''),
    deposit: String(detail.deposit ?? ''),
    shortfall: String(detail.shortfall ?? ''),
  }
}

export const useAfterSalesStore = defineStore('afterSales', () => {
  // State
  const myAfterSales = ref([])
  const myAfterSalesLoading = ref(false)
  const myAfterSalesLoaded = ref(false)
  const adminAfterSales = ref([])
  const adminAfterSalesLoading = ref(false)
  const adminAfterSalesPagination = ref({ page: 1, pageSize: 20, total: 0, pages: 0 })
  const submitting = ref(false)
  const error = ref(null)

  // Getters
  // 打手身份下是否有进行中（PENDING）售后：命中即冻结提现
  const hasPendingBoosterAfterSale = computed(
    () => myAfterSales.value.some(isPendingAfterSale)
  )

  // POST /orders/{id}/after-sales —— body { reason, amount, images: [url] }
  async function createAfterSale(orderId, payload) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post(`/orders/${orderId}/after-sales`, payload)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // GET /orders/{id}/after-sales —— 该单全部售后记录（列表或 {items} 都兼容）
  async function fetchOrderAfterSales(orderId) {
    error.value = null
    try {
      const response = await api.get(`/orders/${orderId}/after-sales`)
      const data = response.data
      const items = Array.isArray(data) ? data : (data?.items ?? [])
      return { success: true, data: items }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  // GET /after-sales/mine?role=requester|booster&status=pending
  async function fetchMyAfterSales(options = {}) {
    myAfterSalesLoading.value = true
    error.value = null
    try {
      const params = {}
      if (options.role) params.role = options.role
      if (options.status) params.status = options.status
      const response = await api.get('/after-sales/mine', { params })
      const data = response.data
      myAfterSales.value = Array.isArray(data) ? data : (data?.items ?? [])
      myAfterSalesLoaded.value = true
      return { success: true, data: myAfterSales.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      myAfterSalesLoading.value = false
    }
  }

  // POST /orders/{id}/after-sale-images（multipart 字段名 image）→ {url,name,size,content_type}
  // 凭证可能仍是手机原图：与交付附件同源的 120s 长超时（见 orders.uploadDeliverAttachment）
  async function uploadAfterSaleImage(orderId, file) {
    error.value = null
    try {
      const form = new FormData()
      form.append('image', file)
      const response = await api.post(`/orders/${orderId}/after-sale-images`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000,
      })
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message, status: err.status }
    }
  }

  // GET /admin/after-sales?status=&order_id=&q=&page=&page_size= → {items,total}
  async function fetchAdminAfterSales(options = {}) {
    adminAfterSalesLoading.value = true
    error.value = null
    try {
      const params = {
        page: options.page || adminAfterSalesPagination.value.page,
        page_size: options.pageSize || adminAfterSalesPagination.value.pageSize,
      }
      if (options.status) params.status = options.status
      if (options.orderId != null && options.orderId !== '') params.order_id = options.orderId
      if (options.q) params.q = options.q
      const response = await api.get('/admin/after-sales', { params })
      adminAfterSales.value = response.data?.items || []
      adminAfterSalesPagination.value = {
        page: Number(response.data?.page) || params.page,
        pageSize: params.page_size,
        total: Number(response.data?.total) || 0,
        pages: Number(response.data?.pages) || 0,
      }
      return { success: true, data: adminAfterSales.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      adminAfterSalesLoading.value = false
    }
  }

  // POST /admin/after-sales/{id}/review —— body { action: 'approve'|'reject'|'close', reason?, amount? }
  // approve 可带 amount（裁定额，默认=申请金额，不得超申请金额）；reject/close 必填 reason
  async function reviewAfterSale(id, payload = {}) {
    submitting.value = true
    error.value = null
    try {
      const body = { action: payload.action }
      if (payload.reason) body.reason = payload.reason
      // 仅 approve 传裁定额：没传时走后端默认（按申请金额扣划）
      if (payload.amount != null) body.amount = payload.amount
      const response = await api.post(`/admin/after-sales/${id}/review`, body)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // POST /orders/{orderId}/after-sales/{afterSaleId}/withdraw —— 老板撤诉（仅 PENDING 可撤）
  async function withdrawAfterSale(orderId, afterSaleId) {
    submitting.value = true
    error.value = null
    try {
      const response = await api.post(`/orders/${orderId}/after-sales/${afterSaleId}/withdraw`)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // POST /orders/{orderId}/after-sales/{afterSaleId}/accept —— 打手同意退款
  // （仅该单打手可调；成功后「可用余额 → 保证金」扣划给老板、售后结案，
  // 返回 status=APPROVED）。可扣款不足时后端 400 + 结构化 detail，
  // 由 parseInsufficientFundsError 解出缺口金额供弹窗提示。
  async function acceptAfterSale(orderId, afterSaleId) {
    submitting.value = true
    error.value = null
    try {
      const response = await api.post(`/orders/${orderId}/after-sales/${afterSaleId}/accept`)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      const result = { success: false, error: err.message }
      const insufficient = parseInsufficientFundsError(err)
      // 只在命中结构化缺口错误时附加字段，保持既有 {success,error} 契约不变
      if (insufficient) result.insufficient = insufficient
      return result
    } finally {
      submitting.value = false
    }
  }

  return {
    myAfterSales,
    myAfterSalesLoading,
    myAfterSalesLoaded,
    adminAfterSales,
    adminAfterSalesLoading,
    adminAfterSalesPagination,
    submitting,
    error,
    hasPendingBoosterAfterSale,
    createAfterSale,
    fetchOrderAfterSales,
    fetchMyAfterSales,
    uploadAfterSaleImage,
    fetchAdminAfterSales,
    reviewAfterSale,
    withdrawAfterSale,
    acceptAfterSale,
  }})
