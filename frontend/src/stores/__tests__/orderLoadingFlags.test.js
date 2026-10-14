import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPut = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    put: (...args) => apiPut(...args),
  },
}))

import { useOrdersStore } from '@/stores/orders'

let pinia

function orderListResponse(items = [], pageSize = 20) {
  return {
    data: {
      items,
      page: 1,
      page_size: pageSize,
      total: items.length,
      pages: 1,
    },
  }
}

function claimsResponse(items = []) {
  return {
    data: {
      items,
      page: 1,
      page_size: 20,
      total: items.length,
      pages: 1,
    },
  }
}

async function flushMicrotasks() {
  await Promise.resolve()
  await Promise.resolve()
  await new Promise((resolve) => setTimeout(resolve, 0))
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  apiGet.mockReset()
  apiPut.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('acceptOrder 不切换全局 loading', () => {
  // 回归：详情页 loading=true 时骨架屏分支会卸载移动端 fixed 操作栏，
  // 接单请求期间整页再白一次。
  it('成功时不改变 store.loading（详情页骨架屏在飞也不关）', async () => {
    const store = useOrdersStore()
    store.loading = true
    apiPut.mockResolvedValue({ data: { id: 42, status: 'LOCKED' } })

    const result = await store.acceptOrder(42)

    expect(result).toEqual({ success: true, data: { id: 42, status: 'LOCKED' } })
    expect(store.loading).toBe(true)
    expect(store.error).toBeNull()
    expect(apiPut).toHaveBeenCalledWith('/orders/42/accept')
  })

  it('成功时也不把 loading 从 false 抬起来', async () => {
    const store = useOrdersStore()
    apiPut.mockResolvedValue({ data: { id: 42, status: 'LOCKED' } })

    await store.acceptOrder(42)

    expect(store.loading).toBe(false)
  })

  it('失败时不改变 store.loading 且写 error', async () => {
    const store = useOrdersStore()
    store.loading = true
    apiPut.mockRejectedValue({ message: '名额已满' })

    const result = await store.acceptOrder(42)

    expect(result).toEqual({ success: false, error: '名额已满' })
    expect(store.loading).toBe(true)
    expect(store.error).toBe('名额已满')
  })

  it('成功时同步更新列表项与 currentOrder', async () => {
    const store = useOrdersStore()
    store.orders = [{ id: 42, status: 'PENDING' }]
    store.currentOrder = { id: 42, status: 'PENDING', title: 'test' }
    apiPut.mockResolvedValue({ data: { id: 42, status: 'LOCKED', title: 'test' } })

    await store.acceptOrder(42)

    expect(store.orders[0].status).toBe('LOCKED')
    expect(store.currentOrder.status).toBe('LOCKED')
  })
})

describe('fetchOrders finally 的请求序号守卫', () => {
  it('序号被更新时，先到的 finally 不把 loading 置 false', async () => {
    const store = useOrdersStore()
    const first = deferred()
    const second = deferred()
    apiGet
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)

    const firstCall = store.fetchOrders()
    const secondCall = store.fetchOrders()
    expect(store.loading).toBe(true)

    first.resolve(orderListResponse([]))
    await firstCall
    // 关键：第二次请求已把 ordersRequestSeq 推到 2，
    // 先落地的旧响应不得提前关掉 loading（否则详情页闪出"订单不存在"空态）
    expect(store.loading).toBe(true)
    expect(store.orders).toEqual([])

    second.resolve(orderListResponse([{ id: 1, status: 'PENDING' }]))
    await secondCall
    expect(store.loading).toBe(false)
    expect(store.orders).toEqual([{ id: 1, status: 'PENDING' }])
  })

  it('非并发时 finally 正常关闭 loading', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([]))

    await store.fetchOrders()

    expect(store.loading).toBe(false)
  })

  it('失败时 finally 也关闭 loading', async () => {
    const store = useOrdersStore()
    apiGet.mockRejectedValueOnce({ message: '网络错误' })

    const result = await store.fetchOrders()

    expect(result).toEqual({ success: false, error: '网络错误' })
    expect(store.error).toBe('网络错误')
    expect(store.loading).toBe(false)
  })

  it('silent 刷新全程不碰 loading', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 1, status: 'PENDING' }]))

    const result = await store.fetchOrders({ silent: true, slim: true })

    expect(result).toEqual({ success: true })
    expect(store.loading).toBe(false)
    expect(apiGet).toHaveBeenCalledWith('/orders/', expect.objectContaining({ params: expect.objectContaining({ slim: 1 }) }))
  })
})

describe('订单列表上下文缓存', () => {
  it('大厅和我的派单并发完成时各自保留列表，不互相覆盖', async () => {
    const store = useOrdersStore()
    const hall = deferred()
    const published = deferred()
    apiGet.mockImplementationOnce(() => hall.promise).mockImplementationOnce(() => published.promise)

    const hallRequest = store.refreshOrdersContext('hall', { page: 1, pageSize: 100, slim: true })
    const publishedRequest = store.refreshOrdersContext('minePublished', { slim: true })

    published.resolve(orderListResponse([{ id: 2, owner: true }]))
    await publishedRequest
    expect(store.ordersContext).toBe('minePublished')
    expect(store.orders.map((order) => order.id)).toEqual([2])

    hall.resolve(orderListResponse([{ id: 1, owner: false }], 100))
    await hallRequest
    expect(store.ordersContext).toBe('minePublished')
    expect(store.orders.map((order) => order.id)).toEqual([2])
    expect(store.ordersByContext.hall.map((order) => order.id)).toEqual([1])
    expect(store.ordersByContext.minePublished.map((order) => order.id)).toEqual([2])
    expect(apiGet.mock.calls[0][1].params.mine_published).toBeUndefined()
    expect(apiGet.mock.calls[1][1].params.mine_published).toBe(true)
  })

  it('有缓存的列表请求 pending 时继续显示旧数据', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 7, title: '原列表' }]))
    await store.refreshOrdersContext('minePublished', { slim: true })

    const pending = deferred()
    apiGet.mockImplementationOnce(() => pending.promise)
    const refresh = store.refreshOrdersContext('minePublished', { slim: true })

    expect(store.loading).toBe(false)
    expect(store.orders).toEqual([{ id: 7, title: '原列表' }])

    pending.resolve(orderListResponse([{ id: 7, title: '更新列表', updated_at: 'new' }]))
    await refresh
    expect(store.orders[0].title).toBe('更新列表')
  })

  it('离开大厅后丢弃排队大厅刷新，不覆盖我的派单列表', async () => {
    const store = useOrdersStore()
    const hall = deferred()
    const published = deferred()
    apiGet.mockImplementationOnce(() => hall.promise).mockImplementationOnce(() => published.promise)

    const hallRequest = store.refreshOrdersContext('hall', { page: 1, pageSize: 100, slim: true })
    expect(await store.fetchOrders({ silent: true, slim: true, page: 1, pageSize: 100 })).toMatchObject({ queued: true })
    const publishedRequest = store.refreshOrdersContext('minePublished', { slim: true })
    published.resolve(orderListResponse([{ id: 9, owner: true }]))
    await publishedRequest

    hall.resolve(orderListResponse([{ id: 8, owner: false }], 100))
    await hallRequest
    await flushMicrotasks()

    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(store.ordersContext).toBe('minePublished')
    expect(store.orders.map((order) => order.id)).toEqual([9])
    expect(store.loading).toBe(false)
  })
})

describe('列表缓存键与 slim 无关', () => {
  // 回归：判断"有没有缓存"的调用没传 slim、真正取数的调用传了 slim，
  // 两个键不一致 → 明明有缓存仍判为没缓存，每次切页都闪骨架屏（白屏）。
  it('带 slim 取数后，不带 slim 的 hasOrdersForContext 也命中', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 11, title: '派单' }]))

    await store.refreshOrdersContext('minePublished', { slim: true })

    expect(store.hasOrdersForContext('minePublished', {})).toBe(true)
  })

  it('大厅同样命中（has 传 page/pageSize/slim，取数只传 slim 也一致）', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 12, title: '大厅单' }], 100))

    await store.refreshOrdersContext('hall', { page: 1, pageSize: 100, slim: true })

    expect(store.hasOrdersForContext('hall', { page: 1, pageSize: 100, slim: true })).toBe(true)
    expect(store.hasOrdersForContext('hall', { page: 1, pageSize: 100 })).toBe(true)
  })

  it('slim 仍然发给后端（只是不参与缓存键）', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([]))

    await store.refreshOrdersContext('hall', { page: 1, pageSize: 100, slim: true })

    expect(apiGet.mock.calls[0][1].params.slim).toBe(1)
  })

  it('不同筛选条件仍是不同缓存（不会错误命中）', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 13 }]))
    store.setFilters({ q: '王者' })
    await store.refreshOrdersContext('minePublished', { slim: true, filters: { q: '王者' } })

    expect(store.hasOrdersForContext('minePublished', { filters: { q: '王者' } })).toBe(true)
    store.setFilters({ q: '英雄联盟' })
    expect(store.hasOrdersForContext('minePublished', { filters: { q: '英雄联盟' } })).toBe(false)
  })
})

describe('primeOrderDetail 详情预填', () => {
  it('列表缓存里有该订单时可直接铺页面（不用骨架屏）', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 21, title: '大厅卡片' }], 100))
    await store.refreshOrdersContext('hall', { page: 1, pageSize: 100, slim: true })

    expect(store.primeOrderDetail(21)).toBe(true)
    expect(store.currentOrder).toEqual({ id: 21, title: '大厅卡片' })
  })

  it('没有任何缓存时返回 false，调用方保留骨架屏', () => {
    const store = useOrdersStore()

    expect(store.primeOrderDetail(999)).toBe(false)
    expect(store.currentOrder).toBeNull()
  })

  it('详情全量快照优先于列表瘦身卡片', async () => {
    const store = useOrdersStore()
    apiGet.mockResolvedValueOnce(orderListResponse([{ id: 22, title: '瘦身卡片', description: null }], 100))
    await store.refreshOrdersContext('hall', { page: 1, pageSize: 100, slim: true })

    apiGet.mockResolvedValueOnce({ data: { id: 22, title: '全量详情', description: '完整需求' } })
    await store.fetchOrder(22)
    store.clearCurrentOrder()

    expect(store.primeOrderDetail(22)).toBe(true)
    expect(store.currentOrder.description).toBe('完整需求')
  })
})

describe('fetchMyClaims request sequence', () => {
  it('旧请求完成不能清除较新 claims 请求的 loading', async () => {
    const store = useOrdersStore()
    const oldRequest = deferred()
    const newRequest = deferred()
    apiGet.mockImplementationOnce(() => oldRequest.promise).mockImplementationOnce(() => newRequest.promise)

    const first = store.fetchMyClaims('CLAIMED', 1, 20, 'old')
    const second = store.fetchMyClaims('CLAIMED', 1, 20, 'new')
    oldRequest.resolve(claimsResponse([{ id: 1 }]))
    expect(await first).toMatchObject({ success: true, stale: true })
    expect(store.myClaimsLoading).toBe(true)

    newRequest.resolve(claimsResponse([{ id: 2 }]))
    await second
    expect(store.myClaimsLoading).toBe(false)
    expect(store.myClaims).toEqual([{ id: 2 }])
  })
})

describe('fetchOrder 不写共享 loading（序号守卫只保护数据）', () => {
  // 回归：共享 loading 归订单列表/动作请求所有；fetchOrder 一旦写它，详情请求
  // 或发布页预填就会把列表骨架、发布按钮等无关 UI 一起锁住/提前解锁。
  it('非 silent 调用（发布页预填走的就是这条路径）在飞期间也不抬升 store.loading', async () => {
    const store = useOrdersStore()
    const pending = deferred()
    apiGet.mockImplementationOnce(() => pending.promise)

    const call = store.fetchOrder(42)
    expect(store.loading).toBe(false)

    pending.resolve({ data: { id: 42, status: 'LOCKED' } })
    expect(await call).toEqual({ success: true, data: { id: 42, status: 'LOCKED' } })

    expect(store.loading).toBe(false)
    expect(store.currentOrder).toEqual({ id: 42, status: 'LOCKED' })
  })

  it('列表刷新在飞时（loading=true）打开详情：详情请求全程不动共享 loading', async () => {
    const store = useOrdersStore()
    store.loading = true
    apiGet.mockResolvedValue({ data: { id: 42, status: 'LOCKED' } })

    const result = await store.fetchOrder(42)

    expect(result.success).toBe(true)
    expect(store.loading).toBe(true)
  })

  it('非 silent 调用失败时不写共享 loading，只写 error', async () => {
    const store = useOrdersStore()
    apiGet.mockRejectedValueOnce({ message: '订单不存在' })

    const result = await store.fetchOrder(42)

    expect(result).toEqual({ success: false, error: '订单不存在' })
    expect(store.loading).toBe(false)
    expect(store.error).toBe('订单不存在')
  })

  it('clearCurrentOrder 作废在飞详情请求：stale、不落地、不污染共享 loading', async () => {
    const store = useOrdersStore()
    const pending = deferred()
    apiGet.mockImplementationOnce(() => pending.promise)

    const call = store.fetchOrder(42)
    expect(store.loading).toBe(false)
    store.clearCurrentOrder()

    pending.resolve({ data: { id: 42, status: 'LOCKED' } })
    const result = await call

    expect(result).toEqual({ success: true, stale: true, data: { id: 42, status: 'LOCKED' } })
    expect(store.currentOrder).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('后续 silent 请求作废在飞的非 silent 详情请求：旧响应不落地也不污染 loading', async () => {
    const store = useOrdersStore()
    const slow = deferred()
    apiGet.mockImplementationOnce(() => slow.promise)

    const slowCall = store.fetchOrder(42)
    expect(store.loading).toBe(false)

    apiGet.mockResolvedValueOnce({ data: { id: 42, status: 'FRESH' } })
    const fresh = await store.fetchOrder(42, { silent: true })
    expect(fresh).toEqual({ success: true, data: { id: 42, status: 'FRESH' } })
    expect(store.loading).toBe(false)

    slow.resolve({ data: { id: 42, status: 'STALE' } })
    const slowResult = await slowCall
    expect(slowResult).toEqual({ success: true, stale: true, data: { id: 42, status: 'STALE' } })
    expect(store.currentOrder).toEqual({ id: 42, status: 'FRESH' })
    expect(store.loading).toBe(false)
  })

  it('被作废的失败请求不写 error，也不污染共享 loading（失败路径序号守卫）', async () => {
    const store = useOrdersStore()
    const slow = deferred()
    apiGet.mockImplementationOnce(() => slow.promise)

    const slowCall = store.fetchOrder(42)
    apiGet.mockResolvedValueOnce({ data: { id: 42, status: 'FRESH' } })
    await store.fetchOrder(42, { silent: true })

    slow.reject({ message: '网络错误' })
    const result = await slowCall

    expect(result).toEqual({ success: false, stale: true, error: '网络错误' })
    expect(store.error).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('silent 调用不改变 store.loading', async () => {
    const store = useOrdersStore()
    store.loading = true
    apiGet.mockResolvedValue({ data: { id: 42, status: 'LOCKED' } })

    const result = await store.fetchOrder(42, { silent: true })

    expect(result).toEqual({ success: true, data: { id: 42, status: 'LOCKED' } })
    expect(store.loading).toBe(true)
    expect(store.currentOrder).toEqual({ id: 42, status: 'LOCKED' })
  })

  it('silent 调用失败时也不改变 store.loading', async () => {
    const store = useOrdersStore()
    store.loading = true
    apiGet.mockRejectedValue({ message: '订单不存在' })

    const result = await store.fetchOrder(42, { silent: true })

    expect(result).toEqual({ success: false, error: '订单不存在' })
    expect(store.loading).toBe(true)
  })

  it('silent 调用仍保留请求序号守卫：旧响应不落地', async () => {
    const store = useOrdersStore()
    const first = deferred()
    apiGet.mockImplementationOnce(() => first.promise)

    const firstCall = store.fetchOrder(42, { silent: true })
    const secondCall = store.fetchOrder(42, { silent: true })

    first.resolve({ data: { id: 42, status: 'STALE' } })
    const firstResult = await firstCall
    expect(firstResult).toEqual({ success: true, stale: true, data: { id: 42, status: 'STALE' } })

    apiGet.mockResolvedValueOnce({ data: { id: 42, status: 'FRESH' } })
    await store.fetchOrder(42, { silent: true })
    await secondCall
    expect(store.currentOrder).toEqual({ id: 42, status: 'FRESH' })
  })
})

describe('fetchOrder 失败结果携带 status（供接单预检区分"被抢走"与网络错误）', () => {
  // 回归：接单预检曾把任何失败（超时/断连/5xx）都翻译成"被其他打手接走或已下架"，
  // 调用方需要 HTTP 状态码才能只对 403/404 说"被抢走"。
  it('403 时返回 status=403 与中文原因', async () => {
    const store = useOrdersStore()
    apiGet.mockRejectedValueOnce({ status: 403, message: '无权访问此订单' })

    const result = await store.fetchOrder(42)

    expect(result).toEqual({ success: false, status: 403, error: '无权访问此订单' })
    expect(store.error).toBe('无权访问此订单')
  })

  it('网络失败（拦截器不带 status）时 status 为 undefined', async () => {
    const store = useOrdersStore()
    apiGet.mockRejectedValueOnce({ message: '网络连接超时，请检查网络后重试' })

    const result = await store.fetchOrder(42)

    expect(result.success).toBe(false)
    expect(result.status).toBeUndefined()
    expect(result.error).toBe('网络连接超时，请检查网络后重试')
  })

  it('被更新请求作废的失败（stale）同样携带 status 且不写共享 error', async () => {
    const store = useOrdersStore()
    const slow = deferred()
    apiGet.mockImplementationOnce(() => slow.promise)

    const slowCall = store.fetchOrder(42)
    apiGet.mockResolvedValueOnce({ data: { id: 42, title: 'fresh' } })
    await store.fetchOrder(42, { silent: true })

    slow.reject({ status: 403, message: '无权访问此订单' })
    const result = await slowCall

    expect(result).toEqual({ success: false, stale: true, status: 403, error: '无权访问此订单' })
    expect(store.error).toBeNull()
  })
})

describe('applyOrderStateChange 同步 currentOrder', () => {
  it('currentOrder 命中时同步 patch status/claim_status/claimed_count', () => {
    const store = useOrdersStore()
    store.orders = [{ id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }]
    store.currentOrder = { id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0, title: 'test' }

    store.applyOrderStateChange({
      order_id: 42,
      status: 'LOCKED',
      claim_status: 'CLAIMED',
      claimed_count: 3,
    })

    expect(store.currentOrder.status).toBe('LOCKED')
    expect(store.currentOrder.claim_status).toBe('CLAIMED')
    expect(store.currentOrder.claimed_count).toBe(3)
    // 未涉及的字段保持原样
    expect(store.currentOrder.title).toBe('test')
    // 列表侧同步
    expect(store.orders[0].status).toBe('LOCKED')
    expect(store.orders[0].claim_status).toBe('CLAIMED')
    expect(store.orders[0].claimed_count).toBe(3)
  })

  it('currentOrder 未命中时保持不动', () => {
    const store = useOrdersStore()
    store.orders = [{ id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }]
    store.currentOrder = { id: 7, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }

    store.applyOrderStateChange({ order_id: 42, status: 'LOCKED', claim_status: 'CLAIMED', claimed_count: 1 })

    expect(store.currentOrder.status).toBe('PENDING')
    expect(store.currentOrder.claimed_count).toBe(0)
    expect(store.orders[0].status).toBe('LOCKED')
  })

  it('列表里没有该订单时也 patch currentOrder（详情页不在大厅列表上下文中）', () => {
    const store = useOrdersStore()
    store.currentOrder = { id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }

    store.applyOrderStateChange({ order_id: '42', status: 'LOCKED', claim_status: 'CLAIMED', claimed_count: 2 })

    expect(store.currentOrder.status).toBe('LOCKED')
    expect(store.currentOrder.claim_status).toBe('CLAIMED')
    expect(store.currentOrder.claimed_count).toBe(2)
  })

  it('currentOrder 为 null 时不抛错', () => {
    const store = useOrdersStore()
    store.orders = [{ id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }]

    expect(() => store.applyOrderStateChange({ order_id: 42, status: 'LOCKED' })).not.toThrow()
    expect(store.orders[0].status).toBe('LOCKED')
  })

  it('change 缺字段时不覆盖已有值', () => {
    const store = useOrdersStore()
    store.orders = [{ id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }]
    store.currentOrder = { id: 42, status: 'PENDING', claim_status: 'OPEN', claimed_count: 0 }

    store.applyOrderStateChange({ order_id: 42, status: 'LOCKED' })

    expect(store.currentOrder.status).toBe('LOCKED')
    expect(store.currentOrder.claim_status).toBe('OPEN')
    expect(store.currentOrder.claimed_count).toBe(0)
  })
})
