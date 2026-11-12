/**
 * 后台「保证金流水」store。
 *
 * 数据源：GET /admin/deposit/transactions
 * 只查 DEPOSIT_TRANSFER_IN / DEPOSIT_TRANSFER_OUT（用户或管理员在
 * 可用余额与保证金之间划转），用于核对“今天有没有人把保证金转回去”。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import api from '@/utils/api'

const emptyStats = {
  in_amount: 0,
  in_count: 0,
  out_amount: 0,
  out_count: 0,
  out_users: 0,
  self_out_amount: 0,
  self_out_count: 0,
  self_out_users: 0,
  net_amount: 0,
}

export const useAdminDepositLedgerStore = defineStore('adminDepositLedger', () => {
  const items = ref([])
  const stats = ref({ ...emptyStats })
  const pagination = ref({ page: 1, pageSize: 20, total: 0, pages: 0 })
  const range = ref({ startDate: '', endDate: '' })
  const loading = ref(false)
  const error = ref('')

  async function fetchLedger(options = {}) {
    loading.value = true
    error.value = ''
    try {
      const params = {
        page: options.page || pagination.value.page,
        page_size: options.pageSize || pagination.value.pageSize,
        direction: options.direction || 'all',
      }
      if (options.startDate) params.start_date = options.startDate
      if (options.endDate) params.end_date = options.endDate
      if (options.q) params.q = options.q

      const response = await api.get('/admin/deposit/transactions', { params })
      const data = response.data || {}
      items.value = data.items || []
      pagination.value = {
        page: data.page || 1,
        pageSize: data.page_size || 20,
        total: data.total || 0,
        pages: data.pages || 0,
      }
      const rawStats = data.stats || {}
      stats.value = {
        in_amount: Number(rawStats.in_amount || 0),
        in_count: Number(rawStats.in_count || 0),
        out_amount: Number(rawStats.out_amount || 0),
        out_count: Number(rawStats.out_count || 0),
        out_users: Number(rawStats.out_users || 0),
        self_out_amount: Number(rawStats.self_out_amount || 0),
        self_out_count: Number(rawStats.self_out_count || 0),
        self_out_users: Number(rawStats.self_out_users || 0),
        net_amount: Number(rawStats.net_amount || 0),
      }
      range.value = {
        startDate: data.start_date || options.startDate || '',
        endDate: data.end_date || options.endDate || '',
      }
      return { success: true, data }
    } catch (err) {
      error.value = err.message || '加载保证金流水失败'
      return { success: false, error: error.value }
    } finally {
      loading.value = false
    }
  }

  function reset() {
    items.value = []
    stats.value = { ...emptyStats }
    pagination.value = { page: 1, pageSize: 20, total: 0, pages: 0 }
    range.value = { startDate: '', endDate: '' }
    error.value = ''
  }

  return {
    items,
    stats,
    pagination,
    range,
    loading,
    error,
    fetchLedger,
    reset,
  }
})
