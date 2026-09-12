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
    bossContact: '',
    q: '',
  })
  const claims = ref([])
  const claimsOrderId = ref(null)
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

  // 详情/报名请求序号：组件复用或快速切换页面时，旧响应不得覆盖当前数据。
  let orderDetailRequestSeq = 0
  let claimsRequestSeq = 0

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

  // 轮询快照对比：这些字段任一变化才需要重渲；更新时间/标题/摘要等
  // 变更必然伴随其中之一（updated_at 会跳）。
  function orderSnapshotKey(order) {
    return [
      order.id,
      order.status,
      order.claim_status,
      order.claimed_count,
      order.updated_at,
      order.deadline || '',
      order.priority ?? '',
      order.accept_available_at || '',
      order.my_claim?.status || '',
      order.compensation_amount ?? '',
      order.title || '',
      order.intro || '',
    ].join('|')
  }

  function isSameOrderSnapshot(prev, next) {
    if (!Array.isArray(prev) || prev.length !== next.length) return false
    for (let i = 0; i < next.length; i += 1) {
      if (orderSnapshotKey(prev[i]) !== orderSnapshotKey(next[i])) return false
    }
    return true
  }

  // options.silent：静默刷新（大厅 30s 轮询用）——不切换 loading 骨架屏、不清空现有数据、失败不弹错误
  // 请求序号守卫：慢网络上旧响应后到会覆盖新筛选结果（搜索"闪回"），
  // 每次发起递增序号，落地的响应若不是最新一次请求则直接丢弃。
  let ordersRequestSeq = 0

  async function fetchOrders(options = {}) {
    const silent = Boolean(options.silent)
    if (!silent) {
      loading.value = true
      error.value = null
    }
    const requestSeq = ++ordersRequestSeq

    const params = {
      page: options.page || pagination.value.page,
      pageSize: options.pageSize || pagination.value.pageSize,
    }

    if (filters.value.gameName) {
      params.game_name = filters.value.gameName
    }

    // 综合搜索：订单号精确命中优先，其次标题/游戏/需求内容（后端按相关度排序）
    if (filters.value.q) {
      params.q = filters.value.q
    }

    if (filters.value.status) {
      params.status = filters.value.status
    }

    // bossContact 只在我的派单（minePublished）上下文生效，避免与大厅共用
    // filters 串扰：大厅轮询不带该参数时不发送残留值
    if (options.minePublished && filters.value.bossContact) {
      params.boss_contact = filters.value.bossContact
    }

    if (options.minePublished) {
      params.mine_published = true
    }

    if (options.bossContact) {
      params.boss_contact = options.bossContact
    }

    // 大厅轮询瘦身：列表卡片用不到的大字段由后端剥掉，减小 JSON 与重绘成本
    if (options.slim) {
      params.slim = 1
    }

    try {
      const response = await api.get('/orders/', { params })

      if (requestSeq !== ordersRequestSeq) {
        return { success: true, stale: true }
      }

      const items = response.data.items
      // 静默轮询快照对比：与当前展示完全一致时跳过整组替换，
      // 避免每 2 秒推倒重渲 20 张卡片（低端手机肉眼可见卡顿）。
      if (silent && isSameOrderSnapshot(orders.value, items)) {
        return { success: true, unchanged: true }
      }

      orders.value = items
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
    const requestSeq = ++orderDetailRequestSeq
    loading.value = true
    error.value = null
    
    try {
      const response = await api.get(`/orders/${orderId}`)
      if (requestSeq !== orderDetailRequestSeq) {
        return { success: true, stale: true, data: response.data }
      }
      currentOrder.value = response.data
      return { success: true, data: response.data }
    } catch (err) {
      if (requestSeq !== orderDetailRequestSeq) {
        return { success: false, stale: true, error: err.message }
      }
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      if (requestSeq === orderDetailRequestSeq) {
        loading.value = false
      }
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
    const requestSeq = ++claimsRequestSeq
    claimsLoading.value = true
    error.value = null
    try {
      const response = await api.get(`/orders/${orderId}/claims`)
      if (requestSeq !== claimsRequestSeq) {
        return { success: true, stale: true, data: response.data?.items ?? [] }
      }
      claims.value = response.data?.items ?? []
      claimsOrderId.value = Number(orderId)
      return { success: true, data: claims.value }
    } catch (err) {
      if (requestSeq !== claimsRequestSeq) {
        return { success: false, stale: true, error: err.message }
      }
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      if (requestSeq === claimsRequestSeq) {
        claimsLoading.value = false
      }
    }
  }

  // 打手自己的接单单：status 可选 'DELIVERED' | 'CLAIMED' | 'SETTLED' | 'CANCELLED'
  let myClaimsRequestSeq = 0

  async function fetchMyClaims(status, page = 1, pageSize = 20, q = '') {
    myClaimsLoading.value = true
    error.value = null
    const requestSeq = ++myClaimsRequestSeq
    try {
      const params = { page, page_size: pageSize }
      if (status) params.status = status
      if (q && q.trim()) params.q = q.trim()
      const response = await api.get('/orders/claims/mine', { params })
      if (requestSeq !== myClaimsRequestSeq) {
        return { success: true, stale: true }
      }
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
        timeout: 120000,
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

  function clearCurrentOrder() {
    orderDetailRequestSeq += 1
    currentOrder.value = null
    claimsRequestSeq += 1
    claims.value = []
    claimsOrderId.value = null
    claimsLoading.value = false
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
    claimsOrderId,
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
    clearCurrentOrder,
    clearError,
  }
})
