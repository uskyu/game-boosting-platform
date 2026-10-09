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

function orderListResponse(items = []) {
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

describe('fetchOrder silent 选项', () => {
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

  it('默认（非 silent）仍按原逻辑切换 loading', async () => {
    const store = useOrdersStore()
    const pending = deferred()
    apiGet.mockImplementationOnce(() => pending.promise)

    const call = store.fetchOrder(42)
    expect(store.loading).toBe(true)

    pending.resolve({ data: { id: 42, status: 'LOCKED' } })
    await call

    expect(store.loading).toBe(false)
    expect(store.currentOrder).toEqual({ id: 42, status: 'LOCKED' })
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
