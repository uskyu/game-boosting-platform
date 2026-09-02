import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/utils/api'

const emptyPagination = { page: 1, pageSize: 20, total: 0, pages: 0 }

export const useAdminUsersStore = defineStore('adminUsers', () => {
  const users = ref([])
  const pagination = ref({ ...emptyPagination })
  const loading = ref(false)
  const error = ref('')

  async function fetchUsers(options = {}) {
    loading.value = true
    error.value = ''
    try {
      const response = await api.get('/admin/users', { params: {
        page: options.page || pagination.value.page, page_size: options.pageSize || pagination.value.pageSize,
        query: options.query || undefined, role: options.role || undefined,
        is_active: options.isActive === '' || options.isActive == null ? undefined : options.isActive,
      } })
      const data = response.data || {}
      users.value = data.items || []
      pagination.value = { page: data.page || 1, pageSize: data.page_size || 20, total: data.total || 0, pages: data.pages || 0 }
      return { success: true, data }
    } catch (err) { error.value = err.message || '加载用户失败'; return { success: false, error: error.value } }
    finally { loading.value = false }
  }

  async function getUser(id) { try { const r = await api.get(`/admin/users/${id}`); return { success: true, data: r.data } } catch (e) { return fail(e) } }
  async function updateUser(id, payload) { try { const r = await api.patch(`/admin/users/${id}`, payload); replace(r.data); return { success: true, data: r.data } } catch (e) { return fail(e) } }
  async function resetPassword(id, password) { try { const r = await api.post(`/admin/users/${id}/reset-password`, { password }); return { success: true, data: r.data } } catch (e) { return fail(e) } }
  async function setStatus(id, isActive) { try { const r = await api.post(`/admin/users/${id}/status`, { is_active: isActive }); replace(r.data); return { success: true, data: r.data } } catch (e) { return fail(e) } }
  async function adjustBalance(id, amount, reason) { try { const r = await api.post(`/admin/users/${id}/adjust-balance`, { amount: Number(amount), reason }); return { success: true, data: r.data } } catch (e) { return fail(e) } }
  async function fetchUserTransactions(id, page = 1) {
    try {
      const r = await api.get(`/admin/users/${id}/transactions`, { params: { page, page_size: 20 } })
      return { success: true, data: r.data }
    } catch (e) { return fail(e) }
  }
  function replace(user) { const index = users.value.findIndex((item) => item.id === user?.id); if (index >= 0) users.value.splice(index, 1, user) }
  function fail(e) { return { success: false, error: e.message || '操作失败' } }
  return { users, pagination, loading, error, fetchUsers, getUser, updateUser, resetPassword, setStatus, adjustBalance, fetchUserTransactions }
})
