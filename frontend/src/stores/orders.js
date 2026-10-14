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
  const ordersContext = ref('hall')
  const ordersByContext = ref({
    hall: [],
    minePublished: [],
  })
  const ordersLoadedByContext = ref({
    hall: false,
    minePublished: false,
  })
  const ordersLoadingByContext = ref({
    hall: false,
    minePublished: false,
  })
  const ordersCacheByKey = ref({})
  const ordersCacheKeyByContext = ref({
    hall: '',
    minePublished: '',
  })
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
  const paginationByContext = ref({
    hall: { page: 1, pageSize: 20, total: 0, pages: 0 },
    minePublished: { page: 1, pageSize: 20, total: 0, pages: 0 },
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
  const myClaimsError = ref(null)
  const myClaimsLoaded = ref(false)
  const myClaimsPagination = ref({ page: 1, pageSize: 20, total: 0, pages: 0 })
  const myClaimsCache = ref({})
  let activeMyClaimsCacheKey = ''
  // 取消协商（双方同意/拒绝，无管理员）：当前订单的协商请求列表
  const cancelRequests = ref([])
  const cancelRequestsOrderId = ref(null)
  const cancelRequestsLoading = ref(false)

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
  let cancelRequestsRequestSeq = 0

  // 看过的订单详情快照（含 description/凭证等全量字段），只保留最近若干条：
  // 再次点开同一订单时先把页面铺满，后台静默补全，避免整页骨架屏（老板侧
  // 跨境链路上详情请求要 1-2 秒，纯骨架看起来就是「点开一片白」）。
  const ORDER_DETAIL_CACHE_LIMIT = 20
  const orderDetailCacheById = ref({})

  function rememberOrderDetail(order) {
    const key = String(order?.id ?? '')
    if (!key) return
    const next = { ...orderDetailCacheById.value, [key]: order }
    const keys = Object.keys(next)
    if (keys.length > ORDER_DETAIL_CACHE_LIMIT) {
      for (const staleKey of keys.slice(0, keys.length - ORDER_DETAIL_CACHE_LIMIT)) {
        delete next[staleKey]
      }
    }
    orderDetailCacheById.value = next
  }

  // 用已有数据（详情缓存优先，其次任意列表缓存里的同 id 卡片）预填 currentOrder。
  // 返回是否命中缓存；命中的调用方应改走静默刷新，别再切成整页骨架。
  function primeOrderDetail(orderId) {
    if (orderId === null || orderId === undefined) return false
    const detailCached = orderDetailCacheById.value[String(orderId)]
    if (detailCached) {
      currentOrder.value = detailCached
      return true
    }
    for (const cache of Object.values(ordersCacheByKey.value)) {
      const listItem = (cache?.items || []).find((item) => Number(item.id) === Number(orderId))
      if (listItem) {
        currentOrder.value = listItem
        return true
      }
    }
    return false
  }

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
      order.cancel_pending ?? '',
      order.my_claim?.cancel_pending ?? '',
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

  // 列表按查询上下文缓存；大厅与我的派单结果绝不共用响应槽位。
  const orderListSeq = new Map()
  const orderListActiveRequests = new Map()
  const orderListVisibleRequests = { hall: 0, minePublished: 0 }
  const orderListSilentQueued = new Map()
  let activeOrdersCacheKey = ''

  function buildOrdersRequest(options, context) {
    const contextPagination = paginationByContext.value[context]
    const params = {
      page: options.page || contextPagination.page,
      pageSize: options.pageSize || contextPagination.pageSize,
    }

    if (context === 'minePublished') {
      if (filters.value.gameName) params.game_name = filters.value.gameName
      if (filters.value.q) params.q = filters.value.q
      if (filters.value.status) params.status = filters.value.status
      if (filters.value.bossContact) params.boss_contact = filters.value.bossContact
    }
    if (options.bossContact) params.boss_contact = options.bossContact
    if (context === 'minePublished') params.mine_published = true
    if (options.slim) params.slim = 1
    return params
  }

  function cacheKeyFor(context, params) {
    // slim 只裁剪响应体字段，不改变「这是哪一批数据」的身份。把它排除在缓存键
    // 之外：否则 hasOrdersForContext（判断有无缓存）与实际写入缓存的请求会算出
    // 两个不同的键，出现「明明有缓存却仍然显示骨架屏」。
    const entries = Object.keys(params)
      .filter((key) => key !== 'slim')
      .sort()
      .map((key) => [key, params[key]])
    return `${context}:${JSON.stringify(entries)}`
  }

  function activateOrdersCache(context, cacheKey) {
    const nextContext = context === 'minePublished' ? 'minePublished' : 'hall'
    ordersContext.value = nextContext
    activeOrdersCacheKey = cacheKey
    const cached = ordersCacheByKey.value[cacheKey]
    orders.value = cached?.items ?? []
    ordersByContext.value[nextContext] = orders.value
    ordersLoadedByContext.value[nextContext] = Boolean(cached)
    pagination.value = cached?.pagination ?? paginationByContext.value[nextContext]
    paginationByContext.value[nextContext] = pagination.value
    loading.value = !cached && Boolean(ordersLoadingByContext.value[nextContext])
    error.value = null
  }

  async function fetchOrders(options = {}) {
    const silent = Boolean(options.silent)
    const context = options.minePublished ? 'minePublished' : 'hall'
    const params = options._frozenParams || buildOrdersRequest(options, context)
    const cacheKey = options._cacheKey || cacheKeyFor(context, params)
    const requestKey = cacheKey
    const activeCount = orderListActiveRequests.get(requestKey) || 0
    if (silent && activeCount > 0) {
      orderListSilentQueued.set(requestKey, { options: { ...options }, params: { ...params }, context, cacheKey })
      return { success: true, skipped: true, queued: true }
    }

    const requestSeq = (orderListSeq.get(requestKey) || 0) + 1
    orderListSeq.set(requestKey, requestSeq)
    orderListActiveRequests.set(requestKey, activeCount + 1)
    activateOrdersCache(context, cacheKey)
    if (!silent) {
      orderListVisibleRequests[context] += 1
      error.value = null
      ordersLoadingByContext.value[context] = true
      if (ordersContext.value === context && !ordersCacheByKey.value[cacheKey]) loading.value = true
    }

    try {
      const response = await api.get('/orders/', {
        params,
        timeout: 10000,
      })

      if (requestSeq !== orderListSeq.get(requestKey)) {
        return { success: true, stale: true }
      }

      const items = response.data.items
      const previous = ordersCacheByKey.value[cacheKey]?.items ?? []
      const nextPagination = {
        page: response.data.page,
        pageSize: response.data.page_size,
        total: response.data.total,
        pages: response.data.pages,
      }
      ordersLoadedByContext.value[context] = true
      if (silent && isSameOrderSnapshot(previous, items)) {
        ordersCacheByKey.value[cacheKey] = {
          items: previous,
          pagination: nextPagination,
        }
        if (activeOrdersCacheKey === cacheKey) {
          orders.value = previous
          pagination.value = nextPagination
        }
        return { success: true, unchanged: true }
      }

      const snapshot = { items, pagination: nextPagination }
      ordersCacheByKey.value[cacheKey] = snapshot
      if (context === 'hall') ordersByContext.value.hall = items
      if (context === 'minePublished') ordersByContext.value.minePublished = items
      paginationByContext.value[context] = nextPagination
      if (activeOrdersCacheKey === cacheKey) {
        orders.value = items
        pagination.value = nextPagination
      }
      return { success: true }
    } catch (err) {
      if ((!silent || options.reportError) && activeOrdersCacheKey === cacheKey) {
        error.value = err.message
      }
      return { success: false, error: err.message }
    } finally {
      const remaining = Math.max(0, (orderListActiveRequests.get(requestKey) || 1) - 1)
      if (remaining) orderListActiveRequests.set(requestKey, remaining)
      else orderListActiveRequests.delete(requestKey)

      if (!silent) {
        orderListVisibleRequests[context] = Math.max(0, orderListVisibleRequests[context] - 1)
      }
      if (requestSeq === orderListSeq.get(requestKey)) {
        ordersLoadingByContext.value[context] = orderListVisibleRequests[context] > 0
      }
      if (activeOrdersCacheKey === cacheKey && orderListVisibleRequests[context] === 0) {
        loading.value = false
      }

      const queued = orderListSilentQueued.get(requestKey)
      if (queued && !orderListActiveRequests.has(requestKey)) {
        orderListSilentQueued.delete(requestKey)
        queueMicrotask(() => {
          if (activeOrdersCacheKey !== queued.cacheKey) return
          fetchOrders({
            ...queued.options,
            ...queued.params,
            silent: true,
            minePublished: queued.context === 'minePublished',
            _frozenParams: queued.params,
            _cacheKey: queued.cacheKey,
          }).catch(() => {})
        })
      }
    }
  }

  function setOrdersContext(context, options = {}) {
    const nextContext = context === 'minePublished' ? 'minePublished' : 'hall'
    if (nextContext === 'minePublished' && options.filters) {
      filters.value = { ...filters.value, ...options.filters }
    }
    if (options.page) {
      paginationByContext.value[nextContext] = {
        ...paginationByContext.value[nextContext],
        page: options.page,
      }
    }
    const params = options.params || buildOrdersRequest(options, nextContext)
    activateOrdersCache(nextContext, options.cacheKey || cacheKeyFor(nextContext, params))
  }

  function hasOrdersForContext(context, options = {}) {
    const nextContext = context === 'minePublished' ? 'minePublished' : 'hall'
    const cacheOptions = { ...options, minePublished: nextContext === 'minePublished' }
    const cacheKey = options.cacheKey || cacheKeyFor(nextContext, options.params || buildOrdersRequest(cacheOptions, nextContext))
    return Boolean(ordersCacheByKey.value[cacheKey])
  }

  async function refreshOrdersContext(context, options = {}) {
    const normalizedContext = context === 'minePublished' ? 'minePublished' : 'hall'
    if (normalizedContext === 'minePublished' && options.filters) {
      filters.value = { ...filters.value, ...options.filters }
    }
    const requestOptions = { ...options, minePublished: normalizedContext === 'minePublished' }
    const params = buildOrdersRequest(requestOptions, normalizedContext)
    const cacheKey = cacheKeyFor(normalizedContext, params)
    activateOrdersCache(normalizedContext, cacheKey)
    return fetchOrders({
      ...requestOptions,
      ...params,
      silent: Boolean(ordersCacheByKey.value[cacheKey]),
      _frozenParams: params,
      _cacheKey: cacheKey,
    })
  }


  // options.silent：调用方语义标记（接单前 freshness 校验、WS 状态变化刷新、
  // 缓存命中后的静默补全）。无论 silent 与否都不写共享 loading：共享 loading
  // 归列表请求（fetchOrders）所有，详情骨架屏由详情页自己的 detailLoading 驱动，
  // 发布页预填等场景需要自己的本地锁（recycleLoading），不能被列表刷新连带锁住。
  // 数据落地仍由请求序号守卫保护：clearCurrentOrder 或后续详情请求会把在飞的
  // 旧响应/旧 finally 作废（stale），不会污染 currentOrder，也不写共享状态。
  async function fetchOrder(orderId, options = {}) {
    const requestSeq = ++orderDetailRequestSeq
    error.value = null

    try {
      const response = await api.get(`/orders/${orderId}`)
      if (requestSeq !== orderDetailRequestSeq) {
        return { success: true, stale: true, data: response.data }
      }
      currentOrder.value = response.data
      rememberOrderDetail(response.data)
      return { success: true, data: response.data }
    } catch (err) {
      // status 用于调用方区分"真的没权限/被抢走"(403/404) 与网络失败
      if (requestSeq !== orderDetailRequestSeq) {
        return { success: false, stale: true, status: err.status, error: err.message }
      }
      error.value = err.message
      return { success: false, status: err.status, error: err.message }
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

  // 打手自己的接单单：按筛选条件缓存，切换 tab 后返回可即时显示最近数据。
  let myClaimsRequestSeq = 0

  async function fetchMyClaims(status, page = 1, pageSize = 20, q = '') {
    const params = { page, page_size: pageSize }
    if (status) params.status = status
    if (q && q.trim()) params.q = q.trim()
    const cacheKey = JSON.stringify(Object.keys(params).sort().map((key) => [key, params[key]]))
    activeMyClaimsCacheKey = cacheKey
    const cached = myClaimsCache.value[cacheKey]
    myClaims.value = cached?.items ?? []
    myClaimsPagination.value = cached?.pagination ?? { page, pageSize, total: 0, pages: 0 }
    myClaimsLoaded.value = Boolean(cached)

    const requestSeq = ++myClaimsRequestSeq
    myClaimsLoading.value = true
    myClaimsError.value = null
    try {
      const response = await api.get('/orders/claims/mine', { params, timeout: 10000 })
      if (requestSeq !== myClaimsRequestSeq) {
        return { success: true, stale: true }
      }
      const data = response.data || {}
      const items = data.items ?? []
      const paginationData = {
        page: data.page ?? page,
        pageSize: data.page_size ?? pageSize,
        total: data.total ?? items.length,
        pages: data.pages ?? 0,
      }
      myClaimsCache.value[cacheKey] = { items, pagination: paginationData }
      myClaimsLoaded.value = true
      if (activeMyClaimsCacheKey === cacheKey) {
        myClaims.value = items
        myClaimsPagination.value = paginationData
      }
      return { success: true, data: items }
    } catch (err) {
      if (requestSeq !== myClaimsRequestSeq) {
        return { success: false, stale: true, error: err.message }
      }
      myClaimsError.value = err.message
      return { success: false, error: err.message }
    } finally {
      if (requestSeq === myClaimsRequestSeq) {
        myClaimsLoading.value = false
      }
    }
  }

  function hasMyClaimsCache(status, page = 1, pageSize = 20, q = '') {
    const params = { page, page_size: pageSize }
    if (status) params.status = status
    if (q && q.trim()) params.q = q.trim()
    const key = JSON.stringify(Object.keys(params).sort().map((name) => [name, params[name]]))
    return Boolean(myClaimsCache.value[key])
  }

  // 审核/驳回交付（发布人或管理员）：payload { action: 'approve' | 'reject', amount?, note?, deduction?, reason? }
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
    // 不切换全局 loading：详情页骨架屏会卸载移动端 fixed 操作栏，
    // 接单请求期间整页再白一次（同 deliverOrder 的做法）。
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
    }
  }

  function applyOrderStateChange(change) {
    const orderId = Number(change?.order_id || 0)
    if (!orderId) return

    const patchOrder = (item) => {
      if (change.status != null) item.status = change.status
      if (change.claim_status != null) item.claim_status = change.claim_status
      if (change.claimed_count != null) item.claimed_count = Number(change.claimed_count)
    }
    for (const cache of Object.values(ordersCacheByKey.value)) {
      const cachedOrder = cache.items.find((item) => Number(item.id) === orderId)
      if (cachedOrder) patchOrder(cachedOrder)
    }
    for (const contextOrders of Object.values(ordersByContext.value)) {
      const contextOrder = contextOrders.find((item) => Number(item.id) === orderId)
      if (contextOrder) patchOrder(contextOrder)
    }
    const order = orders.value.find((item) => Number(item.id) === orderId)
    if (order) patchOrder(order)

    // 详情页打开的订单可能不在当前大厅列表里（筛选后未加载/未挂在大厅），
    // 命中 currentOrder 时同样 patch，让详情页收到订单状态变化。
    if (Number(currentOrder.value?.id) === orderId) {
      if (change.status != null) currentOrder.value.status = change.status
      if (change.claim_status != null) currentOrder.value.claim_status = change.claim_status
      if (change.claimed_count != null) currentOrder.value.claimed_count = Number(change.claimed_count)
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

  function syncOrderListDeliveryAttachments(orderId, attachments) {
    const index = orders.value.findIndex((item) => Number(item.id) === Number(orderId))
    if (index === -1) return

    // Order list items may be slim summaries. Patch only fields they already expose
    // instead of replacing the summary with a full detail response.
    const summary = orders.value[index]
    const updated = { ...summary }
    let changed = false
    if (Object.prototype.hasOwnProperty.call(summary, 'delivery_attachments')) {
      updated.delivery_attachments = [...attachments]
      changed = true
    }
    if (summary.my_claim && typeof summary.my_claim === 'object') {
      updated.my_claim = { ...summary.my_claim, delivery_attachments: [...attachments] }
      changed = true
    }
    if (changed) orders.value[index] = updated
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
      if (Number(currentOrder.value?.id) === Number(orderId)) {
        const claim = currentOrder.value.my_claim
        const savedAttachments = claim
          ? claim.delivery_attachments
          : currentOrder.value.delivery_attachments
        const existing = Array.isArray(savedAttachments) ? [...savedAttachments] : []
        existing.push(response.data)
        const updated = { ...currentOrder.value, delivery_attachments: existing }
        if (claim) {
          updated.my_claim = { ...claim, delivery_attachments: [...existing] }
        }
        currentOrder.value = updated
        syncOrderListDeliveryAttachments(orderId, existing)
      }
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message, status: err.status }
    }
  }

  async function deleteDeliverAttachment(orderId, attachmentIndex) {
    error.value = null
    try {
      const response = await api.delete(`/orders/${orderId}/deliver-attachments/${attachmentIndex}`)
      const responseAttachments = response.data?.my_claim?.delivery_attachments
        ?? response.data?.delivery_attachments
      const claim = Number(currentOrder.value?.id) === Number(orderId)
        ? currentOrder.value.my_claim
        : null
      const currentSavedAttachments = claim
        ? claim.delivery_attachments
        : currentOrder.value?.delivery_attachments
      const currentAttachments = Array.isArray(currentSavedAttachments) ? currentSavedAttachments : []
      const attachments = Array.isArray(responseAttachments)
        ? [...responseAttachments]
        : currentAttachments.filter((_, index) => index !== Number(attachmentIndex))
      const responseClaim = response.data?.my_claim
      if (Number(currentOrder.value?.id) === Number(orderId)) {
        const updated = { ...currentOrder.value, delivery_attachments: [...attachments] }
        if (claim || responseClaim) {
          updated.my_claim = {
            ...(claim || {}),
            ...(responseClaim || {}),
            delivery_attachments: [...attachments],
          }
        }
        currentOrder.value = updated
      }
      syncOrderListDeliveryAttachments(orderId, attachments)
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

  // ── 取消协商（双方直接同意/拒绝，无管理员裁决）───────────────────
  // 请求序号 + 独立 loading：详情页每次加载都会拉，不能用全局 loading
  // （会把协商弹窗连同骨架屏一起卸载）。
  async function fetchCancelRequests(orderId) {
    const requestSeq = ++cancelRequestsRequestSeq
    cancelRequestsLoading.value = true
    error.value = null
    try {
      const response = await api.get(`/orders/${orderId}/cancel-requests`)
      if (requestSeq !== cancelRequestsRequestSeq) {
        return { success: true, stale: true, data: [] }
      }
      const items = response.data?.items ?? []
      cancelRequests.value = items
      cancelRequestsOrderId.value = Number(orderId)
      return { success: true, data: items }
    } catch (err) {
      if (requestSeq !== cancelRequestsRequestSeq) {
        return { success: false, stale: true, error: err.message }
      }
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      if (requestSeq === cancelRequestsRequestSeq) {
        cancelRequestsLoading.value = false
      }
    }
  }

  // 发起取消协商：payload { claim_id, reason, compensation_amount }
  // 发单员必须选 claim_id；打手只能为自己的名额提交。
  async function createCancelRequest(orderId, payload) {
    error.value = null
    try {
      const response = await api.post(`/orders/${orderId}/cancel-requests`, payload)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  // 处理取消协商：payload { action: 'approve'|'reject', decision_reason? }
  // 仅收件方可操作；approve 会取消该名额并按约定金额划转资金。
  async function decideCancelRequest(orderId, requestId, payload) {
    error.value = null
    try {
      const response = await api.post(
        `/orders/${orderId}/cancel-requests/${requestId}/decision`,
        payload,
      )
      const updated = response.data
      const index = cancelRequests.value.findIndex(
        (item) => Number(item.id) === Number(requestId),
      )
      // 空值兜底：后端偶发返回空体（null/undefined）时不改动列表，
      // 否则会把 null 插进协商记录，页面读 request.status 直接崩。
      if (index !== -1 && updated != null) {
        cancelRequests.value.splice(index, 1, updated)
      }
      return { success: true, data: updated }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
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
    paginationByContext.value[ordersContext.value] = {
      ...paginationByContext.value[ordersContext.value],
      page,
    }
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
    cancelRequestsRequestSeq += 1
    cancelRequests.value = []
    cancelRequestsOrderId.value = null
    cancelRequestsLoading.value = false
  }

  function clearError() {
    error.value = null
  }

  return {
    // State
    orders,
    ordersContext,
    ordersByContext,
    ordersLoadedByContext,
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
    myClaimsError,
    myClaimsPagination,
    cancelRequests,
    cancelRequestsOrderId,
    cancelRequestsLoading,
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
    setOrdersContext,
    hasOrdersForContext,
    refreshOrdersContext,
    fetchOrder,
    primeOrderDetail,
    editOrder,
    fetchClaims,
    fetchMyClaims,
    hasMyClaimsCache,
    reviewClaim,
    acceptOrder,
    applyOrderStateChange,
    deliverOrder,
    uploadDeliverAttachment,
    deleteDeliverAttachment,
    confirmOrder,
    disputeOrder,
    cancelOrder,
    fetchCancelRequests,
    createCancelRequest,
    decideCancelRequest,
    payOrder,
    refundOrder,
    setFilters,
    setPage,
    clearAnalysisResult,
    clearCurrentOrder,
    clearError,
  }
})
