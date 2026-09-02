/**
 * Orders store using Pinia.
 * Manages order state and operations.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/utils/api'

export const useOrdersStore = defineStore('orders', () => {
  // State
  const orders = ref([])
  const currentOrder = ref(null)
  const analysisResult = ref(null)
  const loading = ref(false)
  const analyzing = ref(false)
  const error = ref(null)
  const pagination = ref({
    page: 1,
    pageSize: 20,
    total: 0,
    pages: 0,
  })
  const filters = ref({
    gameName: '',
    status: '',
  })
  const claims = ref([])
  const claimsLoading = ref(false)
  // 打手自己的接单单（GET /orders/claims/mine）
  const myClaims = ref([])
  const myClaimsLoading = ref(false)
  const myClaimsPagination = ref({ page: 1, pageSize: 20, total: 0, pages: 0 })

  // Getters
  const hasOrders = computed(() => orders.value.length > 0)
  const pendingOrders = computed(() => 
    orders.value.filter(o => o.status === 'PENDING')
  )
  const lockedOrders = computed(() => 
    orders.value.filter(o => o.status === 'LOCKED')
  )
  const deliveredOrders = computed(() =>
    orders.value.filter(o => o.status === 'DELIVERED')
  )
  const completedOrders = computed(() =>
    orders.value.filter(o => o.status === 'COMPLETED')
  )

  // Actions
  async function analyzeRequirement(description) {
    analyzing.value = true
    error.value = null
    analysisResult.value = null
    
    try {
      const response = await api.post('/orders/analyze', {
        description,
      })
      
      analysisResult.value = response.data
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      analyzing.value = false
    }
  }

  async function createOrder(orderData) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.post('/orders/create', orderData)
      
      // Add new order to the beginning of the list
      orders.value.unshift(response.data)
      
      // Clear analysis result
      analysisResult.value = null
      
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  // options.silent：静默刷新（大厅 30s 轮询用）——不切换 loading 骨架屏、不清空现有数据、失败不弹错误
  async function fetchOrders(options = {}) {
    const silent = Boolean(options.silent)
    if (!silent) {
      loading.value = true
      error.value = null
    }

    const params = {
      page: options.page || pagination.value.page,
      pageSize: options.pageSize || pagination.value.pageSize,
    }

    if (filters.value.gameName) {
      params.game_name = filters.value.gameName
    }

    if (filters.value.status) {
      params.status = filters.value.status
    }

    if (options.minePublished) {
      params.mine_published = true
    }

    try {
      const response = await api.get('/orders/', { params })

      orders.value = response.data.items
      pagination.value = {
        page: response.data.page,
        pageSize: response.data.page_size,
        total: response.data.total,
        pages: response.data.pages,
      }

      return { success: true }
    } catch (err) {
      if (!silent) {
        error.value = err.message
      }
      return { success: false, error: err.message }
    } finally {
      if (!silent) {
        loading.value = false
      }
    }
  }

  async function fetchOrder(orderId) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.get(`/orders/${orderId}`)
      currentOrder.value = response.data
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function editOrder(orderId, payload) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}`, payload)

      // Update order in list
      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function fetchClaims(orderId) {
    claimsLoading.value = true
    error.value = null
    try {
      const response = await api.get(`/orders/${orderId}/claims`)
      claims.value = response.data?.items ?? []
      return { success: true, data: claims.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      claimsLoading.value = false
    }
  }

  // 打手自己的接单单：status 可选 'DELIVERED' | 'CLAIMED' | 'SETTLED'
  async function fetchMyClaims(status, page = 1, pageSize = 20) {
    myClaimsLoading.value = true
    error.value = null
    try {
      const params = { page, page_size: pageSize }
      if (status) params.status = status
      const response = await api.get('/orders/claims/mine', { params })
      const data = response.data || {}
      myClaims.value = data.items ?? []
      myClaimsPagination.value = {
        page: data.page ?? page,
        pageSize: data.page_size ?? pageSize,
        total: data.total ?? myClaims.value.length,
        pages: data.pages ?? 0,
      }
      return { success: true, data: myClaims.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      myClaimsLoading.value = false
    }
  }

  // 审核打款（发布人或管理员）：payload { action: 'approve', amount?, note?, deduction? }
  // deduction：炸单扣除的赔偿金（0 ≤ deduction ≤ compensation_amount，缺省不扣）
  async function reviewClaim(orderId, claimId, payload) {
    loading.value = true
    error.value = null
    try {
      const response = await api.put(`/orders/${orderId}/claims/${claimId}/review`, payload)
      const updated = response.data
      const index = claims.value.findIndex((claim) => claim.id === claimId)
      if (index !== -1) {
        claims.value.splice(index, 1, updated)
      }
      return { success: true, data: updated }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function acceptOrder(orderId) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.put(`/orders/${orderId}/accept`)
      
      // Update order in list
      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }
      
      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }
      
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function deliverOrder(orderId, deliveryNote) {
    // 不切换全局 loading：详情页骨架屏会卸载交付弹窗，丢失其关闭事件
    error.value = null

    try {
      const payload = deliveryNote ? { delivery_note: deliveryNote, notes: deliveryNote } : undefined
      const response = payload
        ? await api.put(`/orders/${orderId}/deliver`, payload)
        : await api.put(`/orders/${orderId}/deliver`)

      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function uploadDeliverAttachment(orderId, file) {
    error.value = null
    try {
      const form = new FormData()
      form.append('attachment', file)
      const response = await api.post(`/orders/${orderId}/deliver-attachments`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      if (currentOrder.value?.id === orderId) {
        const existing = Array.isArray(currentOrder.value.delivery_attachments) ? [...currentOrder.value.delivery_attachments] : []
        existing.push(response.data)
        currentOrder.value = { ...currentOrder.value, delivery_attachments: existing }
        const idx = orders.value.findIndex(o => o.id === orderId)
        if (idx !== -1) orders.value[idx] = { ...orders.value[idx], delivery_attachments: [...existing] }
      }
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function deleteDeliverAttachment(orderId, attachmentIndex) {
    error.value = null
    try {
      const response = await api.delete(`/orders/${orderId}/deliver-attachments/${attachmentIndex}`)
      const idx = orders.value.findIndex(o => o.id === orderId)
      if (idx !== -1) orders.value[idx] = response.data
      if (currentOrder.value?.id === orderId) currentOrder.value = response.data
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  // payload: { amount?: number|string, note?: string } —— amount 缺省全额结算
  async function confirmOrder(orderId, payload = null) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}/confirm`, payload || {})

      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function disputeOrder(orderId, reason = '') {
    loading.value = true
    error.value = null

    try {
      const params = reason ? { reason } : {}
      const response = await api.put(`/orders/${orderId}/dispute`, null, { params })

      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function cancelOrder(orderId) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}/cancel`)

      // Update order in list
      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function payOrder(orderId) {
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}/pay`)

      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function refundOrder(orderId) {
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}/refund`)

      const index = orders.value.findIndex(o => o.id === orderId)
      if (index !== -1) {
        orders.value[index] = response.data
      }

      if (currentOrder.value?.id === orderId) {
        currentOrder.value = response.data
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  function setFilters(newFilters) {
    filters.value = { ...filters.value, ...newFilters }
  }

  function setPage(page) {
    pagination.value.page = page
  }

  function clearAnalysisResult() {
    analysisResult.value = null
  }

  function clearError() {
    error.value = null
  }

  return {
    // State
    orders,
    currentOrder,
    analysisResult,
    loading,
    analyzing,
    error,
    pagination,
    filters,
    claims,
    claimsLoading,
    myClaims,
    myClaimsLoading,
    myClaimsPagination,
    // Getters
    hasOrders,
    pendingOrders,
    lockedOrders,
    deliveredOrders,
    completedOrders,
    // Actions
    analyzeRequirement,
    createOrder,
    fetchOrders,
    fetchOrder,
    editOrder,
    fetchClaims,
    fetchMyClaims,
    reviewClaim,
    acceptOrder,
    deliverOrder,
    uploadDeliverAttachment,
    deleteDeliverAttachment,
    confirmOrder,
    disputeOrder,
    cancelOrder,
    payOrder,
    refundOrder,
    setFilters,
    setPage,
    clearAnalysisResult,
    clearError,
  }
})
