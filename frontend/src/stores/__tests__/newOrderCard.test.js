import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    put: vi.fn(),
    post: vi.fn(),
  },
}))

import { useOrdersStore } from '@/stores/orders'
import { useNotificationsStore } from '@/stores/notifications'
import { useAuthStore } from '@/stores/auth'

// vitest 环境是 node：给 auth store 一个最小的 localStorage 垫片
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
}

let pinia

function hallListResponse(items, { page = 1, total } = {}) {
  return {
    data: {
      items,
      page,
      page_size: 100,
      total: total ?? items.length,
      pages: 1,
    },
  }
}

// 服务端直推的 slim 卡片：viewer 相关字段（accept_available_at）为 null。
function makeCard(id) {
  return {
    id,
    status: 'PENDING',
    claim_status: 'OPEN',
    claimed_count: 0,
    accept_available_at: null,
    title: `新订单 ${id}`,
    updated_at: '2026-10-14T02:00:00Z',
  }
}

async function seedHall(store, items, options) {
  apiGet.mockResolvedValueOnce(hallListResponse(items, options))
  await store.refreshOrdersContext('hall', { silent: true, slim: true, page: 1, pageSize: 100 })
}

function authenticate() {
  const auth = useAuthStore()
  auth.setTokens('access-token', 'refresh-token')
  auth.setUser({ id: 5, role: 'BOOSTER' })
  return auth
}

beforeEach(() => {
  apiGet.mockReset()
  storage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('applyNewOrderCard 本地插卡', () => {
  it('按 id 去重后把新单插进大厅第一页缓存头部，并自洽抬 total', async () => {
    const store = useOrdersStore()
    await seedHall(store, [makeCard(1), makeCard(2)])
    expect(store.orders.map((order) => order.id)).toEqual([1, 2])
    expect(store.pagination.total).toBe(2)

    const inserted = store.applyNewOrderCard(makeCard(9))

    expect(inserted).toBe(true)
    expect(store.orders.map((order) => order.id)).toEqual([9, 1, 2])
    expect(store.pagination.total).toBe(3)

    // 重复通知同一单：no-op，不产生重复卡片
    expect(store.applyNewOrderCard(makeCard(9))).toBe(false)
    expect(store.orders).toHaveLength(3)
  })

  it('不动 hallDigest 基线（摘要会在下轮发现变化并全量对账，自愈路径）', async () => {
    const store = useOrdersStore()
    await seedHall(store, [makeCard(1)])
    expect(store.hallDigest).toBeNull()

    store.applyNewOrderCard(makeCard(9))

    expect(store.hallDigest).toBeNull()
  })

  it('当前不在大厅上下文（我的派单等）时 no-op', async () => {
    const store = useOrdersStore()
    await seedHall(store, [makeCard(1)])

    apiGet.mockResolvedValueOnce(hallListResponse([{ id: 7 }], { total: 1 }))
    await store.refreshOrdersContext('minePublished')
    expect(store.ordersContext).toBe('minePublished')

    expect(store.applyNewOrderCard(makeCard(9))).toBe(false)
  })

  it('没有大厅缓存时 no-op', () => {
    const store = useOrdersStore()
    expect(store.applyNewOrderCard(makeCard(9))).toBe(false)
  })

  it('激活的不是第一页缓存（翻页到第 2 页）时 no-op', async () => {
    const store = useOrdersStore()
    await seedHall(store, [makeCard(1)], { page: 2 })

    expect(store.applyNewOrderCard(makeCard(9))).toBe(false)
    expect(store.orders.map((order) => order.id)).toEqual([1])
  })

  it('非法卡片（缺 id）时 no-op', async () => {
    const store = useOrdersStore()
    await seedHall(store, [makeCard(1)])
    expect(store.applyNewOrderCard(null)).toBe(false)
    expect(store.applyNewOrderCard({ title: '无 id' })).toBe(false)
  })
})

describe('新单通知 signalNewOrderNotification', () => {
  it('带 order_card 时不 bump 版本（不触发大厅全量刷新），列表即时出现该单', async () => {
    const orders = useOrdersStore()
    await seedHall(orders, [makeCard(1)])
    authenticate()
    const notifications = useNotificationsStore()
    const versionBefore = notifications.newOrderNotificationVersion

    const handled = notifications.handleRealtimeNotification({
      id: 100,
      type: 'SYSTEM_ANNOUNCEMENT',
      title: '有新订单啦',
      order_card: makeCard(9),
    })

    expect(handled).toBe(true)
    // 关键：不再 bump → OrderHallView 的 watch 不会触发全量 silentRefresh
    expect(notifications.newOrderNotificationVersion).toBe(versionBefore)
    // 新单即时可见
    expect(orders.orders[0].id).toBe(9)
  })

  it('没有 order_card（老服务器/其他来源）时保持旧行为：bump 版本走全量刷', async () => {
    const orders = useOrdersStore()
    await seedHall(orders, [makeCard(1)])
    authenticate()
    const notifications = useNotificationsStore()
    const versionBefore = notifications.newOrderNotificationVersion

    notifications.handleRealtimeNotification({
      id: 200,
      type: 'SYSTEM_ANNOUNCEMENT',
      title: '有新订单啦',
    })

    expect(notifications.newOrderNotificationVersion).toBe(versionBefore + 1)
    // 列表没被本地改动，等大厅 watch 全量刷
    expect(orders.orders.map((order) => order.id)).toEqual([1])
  })

  it('同一通知只 signal 一次：重复到达不再 bump 也不再插卡', async () => {
    const orders = useOrdersStore()
    await seedHall(orders, [makeCard(1)])
    authenticate()
    const notifications = useNotificationsStore()

    notifications.handleRealtimeNotification({
      id: 300,
      type: 'SYSTEM_ANNOUNCEMENT',
      title: '有新订单啦',
      order_card: makeCard(9),
    })
    const versionAfterFirst = notifications.newOrderNotificationVersion
    const lengthAfterFirst = orders.orders.length

    notifications.handleRealtimeNotification({
      id: 300,
      type: 'SYSTEM_ANNOUNCEMENT',
      title: '有新订单啦',
      order_card: makeCard(9),
    })

    expect(notifications.newOrderNotificationVersion).toBe(versionAfterFirst)
    expect(orders.orders.length).toBe(lengthAfterFirst)
  })
})
