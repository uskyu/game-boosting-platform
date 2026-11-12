/**
 * 发单权限（publish permission）store。
 * 覆盖：我的权限/最近申请查询、自助申请与撤回、后台申请列表与审核。
 * 接口契约见后端 publish_permission 端点（已冻结）。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/utils/api'

export const PUBLISH_PERMISSION_STATUS_META = {
  PENDING: { label: '待审核', tagClass: '!bg-warning-soft !text-warning' },
  APPROVED: { label: '已通过', tagClass: '!bg-success-soft !text-success' },
  REJECTED: { label: '已驳回', tagClass: '!bg-danger-soft !text-danger' },
  WITHDRAWN: { label: '已撤回', tagClass: '!bg-surface-3 !text-ink-2' },
}

export const PUBLISH_PERMISSION_STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '待审核' },
  { value: 'APPROVED', label: '已通过' },
  { value: 'REJECTED', label: '已驳回' },
  { value: 'WITHDRAWN', label: '已撤回' },
]

export function getPublishPermissionStatusLabel(status) {
  return PUBLISH_PERMISSION_STATUS_META[status]?.label || status || '未知状态'
}

export function getPublishPermissionStatusTagClass(status) {
  return PUBLISH_PERMISSION_STATUS_META[status]?.tagClass || '!bg-surface-3 !text-ink-2'
}

const emptyMine = {
  can_publish: false,
  apply_enabled: true,
  // 全站发单权限管控是否开启（默认开启，与站点设置一致）
  required: true,
  // 个人发单封禁（显式封禁在两种模式下都阻止发单）
  publish_banned: false,
  // 后端权威快照：当前用户是否真的可以发单
  can_publish_effective: false,
  latest: null,
  has_pending: false,
}

export const usePublishPermissionStore = defineStore('publishPermission', () => {
  // State
  const mine = ref({ ...emptyMine })
  const adminRequests = ref([])
  const adminPagination = ref({ page: 1, pageSize: 20, total: 0, pages: 0 })
  const loading = ref(false)
  const submitting = ref(false)
  const error = ref(null)

  // GET /publish-permission/mine
  async function fetchMine() {
    loading.value = true
    error.value = null
    try {
      const response = await api.get('/publish-permission/mine')
      const data = response.data || {}
      // can_publish_effective 是后端权威快照；旧响应缺少该字段时回退到
      // 原始开通标记（无封禁字段时视为未封禁），避免把已开通用户误判成未开通。
      const canPublishEffective = typeof data.can_publish_effective === 'boolean'
        ? data.can_publish_effective
        : data.can_publish === true && data.publish_banned !== true
      mine.value = {
        can_publish: data.can_publish === true,
        // 未返回时按开启处理（与站点默认一致）
        apply_enabled: data.apply_enabled !== false,
        required: data.required !== false,
        publish_banned: data.publish_banned === true,
        can_publish_effective: canPublishEffective === true,
        latest: data.latest || null,
        has_pending: data.has_pending === true || data.latest?.status === 'PENDING',
      }
      return { success: true, data: mine.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  // POST /publish-permission/apply —— body { reason? }（≤500）
  async function apply(reason) {
    submitting.value = true
    error.value = null
    try {
      const body = {}
      const trimmed = (reason || '').trim()
      if (trimmed) body.reason = trimmed
      const response = await api.post('/publish-permission/apply', body)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // POST /publish-permission/{id}/withdraw
  async function withdraw(id) {
    submitting.value = true
    error.value = null
    try {
      const response = await api.post(`/publish-permission/${id}/withdraw`)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  // GET /admin/publish-permission/requests?status=&q=&page=&page_size=
  async function fetchAdminRequests(options = {}) {
    loading.value = true
    error.value = null
    try {
      const params = {
        page: options.page || adminPagination.value.page,
        page_size: options.pageSize || adminPagination.value.pageSize,
      }
      if (options.status) params.status = options.status
      if (options.q && options.q.trim()) params.q = options.q.trim()
      const response = await api.get('/admin/publish-permission/requests', { params })
      const data = response.data || {}
      adminRequests.value = data.items || []
      adminPagination.value = {
        page: Number(data.page) || params.page,
        pageSize: params.page_size,
        total: Number(data.total) || 0,
        pages: Number(data.pages) || 0,
      }
      return { success: true, data: adminRequests.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  // 换号/重新挂载时清空上一账号或上一管理员的残留状态
  function reset() {
    mine.value = { ...emptyMine }
    adminRequests.value = []
    adminPagination.value = { page: 1, pageSize: 20, total: 0, pages: 0 }
    loading.value = false
    submitting.value = false
    error.value = null
  }

  // POST /admin/publish-permission/requests/{id}/review —— body { action, reason? }
  async function reviewRequest(id, payload = {}) {
    submitting.value = true
    error.value = null
    try {
      const body = { action: payload.action }
      const trimmed = (payload.reason || '').trim()
      if (trimmed) body.reason = trimmed
      const response = await api.post(`/admin/publish-permission/requests/${id}/review`, body)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  return {
    mine,
    adminRequests,
    adminPagination,
    loading,
    submitting,
    error,
    fetchMine,
    apply,
    withdraw,
    fetchAdminRequests,
    reviewRequest,
    reset,
  }
})
