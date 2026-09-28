/**
 * 提现打款批次（后台）：批次列表、建批、明细、导出 .xls、导入银行回执。
 * 所有 action 都返回 { success, data|error } 且不抛错，错误文案由 api 拦截层
 * 转成可读中文（后端 400 的 detail 直接透出）。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/utils/api'
import { downloadBlobFile, parseContentDispositionFilename } from '@/utils/downloadFile'

// 回执文件上限 5MB
const MAX_RECEIPT_FILE_SIZE = 5 * 1024 * 1024
const EXPORT_MIME_TYPE = 'application/vnd.ms-excel'

function toInt(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback
}

// axios 的 headers key 视运行环境可能大小写不一，统一按小写找
function readContentDisposition(headers) {
  if (!headers) {
    return ''
  }
  const direct = headers['content-disposition']
  if (direct) {
    return direct
  }
  const key = Object.keys(headers).find((name) => name.toLowerCase() === 'content-disposition')
  return key ? headers[key] : ''
}

export const useWithdrawalPayoutStore = defineStore('withdrawalPayout', () => {
  // State
  const batches = ref([])
  const batchesLoading = ref(false)
  const batchesPagination = ref({ page: 1, page_size: 20, total: 0, pages: 0 })
  const currentBatch = ref(null)
  const currentBatchLoading = ref(false)
  const submitting = ref(false)
  const error = ref('')

  // Actions
  async function fetchBatches(options = {}) {
    batchesLoading.value = true
    error.value = ''

    try {
      const pageSize = Math.max(1, toInt(options.pageSize ?? options.page_size, batchesPagination.value.page_size))
      const response = await api.get('/admin/withdrawals/payout-batches', {
        params: {
          page: Math.max(1, toInt(options.page, batchesPagination.value.page)),
          page_size: pageSize,
        },
      })

      batches.value = response.data?.items || []
      batchesPagination.value = {
        page: Math.max(1, toInt(response.data?.page, 1)),
        page_size: Math.max(1, toInt(response.data?.page_size, pageSize)),
        total: toInt(response.data?.total, 0),
        pages: toInt(response.data?.pages, 0),
      }
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      batchesLoading.value = false
    }
  }

  async function createBatch({ ids, category, remark } = {}) {
    submitting.value = true
    error.value = ''

    try {
      const body = {
        ids: (Array.isArray(ids) ? ids : [])
          .map((id) => Number(id))
          // 过滤掉 undefined/null/小数/非正数，避免把 0 这种脏 id 发给后端
          .filter((id) => Number.isInteger(id) && id > 0),
        category,
      }
      const remarkText = typeof remark === 'string' ? remark.trim() : ''
      if (remarkText) {
        body.remark = remarkText
      }
      const response = await api.post('/admin/withdrawals/payout-batches', body)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function fetchBatch(id) {
    currentBatchLoading.value = true
    error.value = ''

    try {
      const response = await api.get(`/admin/withdrawals/payout-batches/${id}`)
      currentBatch.value = response.data || null
      return { success: true, data: currentBatch.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      currentBatchLoading.value = false
    }
  }

  // 导出批次付款文件（binary + Content-Disposition 命名），失败不落盘
  async function exportBatch(id) {
    submitting.value = true
    error.value = ''

    try {
      const response = await api.get(`/admin/withdrawals/payout-batches/${id}/export.xls`, {
        responseType: 'arraybuffer',
      })
      const blob = new Blob([response.data], { type: EXPORT_MIME_TYPE })
      const filename =
        parseContentDispositionFilename(readContentDisposition(response.headers)) ||
        `支付宝批量付款_${id}.xls`
      downloadBlobFile(blob, filename)
      return { success: true, filename }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // 导入银行回执：POST multipart file，返回逐行匹配结果
  async function importReceipt(id, file) {
    if (!file) {
      error.value = '请选择回执文件'
      return { success: false, error: error.value }
    }
    if (Number(file.size) > MAX_RECEIPT_FILE_SIZE) {
      error.value = '回执文件不能超过 5MB'
      return { success: false, error: error.value }
    }
    const fileName = String(file.name || '').toLowerCase()
    if (!fileName.endsWith('.xls') && !fileName.endsWith('.csv')) {
      error.value = '仅支持 .xls 或 .csv 回执文件'
      return { success: false, error: error.value }
    }

    submitting.value = true
    error.value = ''

    try {
      const form = new FormData()
      form.append('file', file)
      const response = await api.post(
        `/admin/withdrawals/payout-batches/${id}/import-receipt`,
        form,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  function clearError() {
    error.value = ''
  }

  return {
    // State
    batches,
    batchesLoading,
    batchesPagination,
    currentBatch,
    currentBatchLoading,
    submitting,
    error,
    // Actions
    fetchBatches,
    createBatch,
    fetchBatch,
    exportBatch,
    importReceipt,
    clearError,
  }
})
