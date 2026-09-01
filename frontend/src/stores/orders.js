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

  async function fetchOrders(options = {}) {
    loading.value = true
    error.value = null
    
    const params = {
      page: options.page || pagination.value.page,
      page_size: options.pageSize || pagination.value.pageSize,
    }
    
    if (filters.value.gameName) {
      params.game_name = filters.value.gameName
    }
    
    if (filters.value.status) {
      params.status = filters.value.status
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
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
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

  async function deliverOrder(orderId) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}/deliver`)

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

  async function confirmOrder(orderId) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/orders/${orderId}/confirm`)

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
    acceptOrder,
    deliverOrder,
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
