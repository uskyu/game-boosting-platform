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

const playNewOrderOnceForOrders = vi.fn()
vi.mock('@/utils/newOrderChime', () => ({
  playNewOrderOnceForOrders: (...args) => playNewOrderOnceForOrders(...args),
}))

import { useOrdersStore } from '@/stores/orders'
import { useSettingsStore } from '@/stores/settings'

// vitest 环境是 node：auth store 初始化要读 localStorage
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => (storage.has(key) ? storage.get(key) : null),
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
}

const NOW = Date.now()

function order(id, createdAt = new Date(NOW).toISOString()) {
  return {
    id,
    status: 'PENDING',
    claim_status: 'OPEN',
    claimed_count: 0,
    title: `新订单 ${id}`,
    created_at: createdAt,
    updated_at: createdAt,
  }
}

function hallResponse(items) {
  return {
    data: {
      items,
      page: 1,
      page_size: 100,
      total: items.length,
      pages: 1,
    },
  }
}

async function refreshHall(items) {
  apiGet.mockResolvedValueOnce(hallResponse(items))
  return useOrdersStore().refreshOrdersContext('hall', {
    silent: true,
    slim: true,
    page: 1,
    pageSize: 100,
  })
}

let pinia

beforeEach(() => {
  apiGet.mockReset()
  playNewOrderOnceForOrders.mockReset()
  storage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  disposePinia(pinia)
})

describe('hall list new-order sound fallback', () => {
  it('rings once for a genuinely new fresh order on a background refresh', async () => {
    await refreshHall([order(1, new Date(NOW - 60_000).toISOString())])
    expect(playNewOrderOnceForOrders).not.toHaveBeenCalled()

    await refreshHall([order(2), order(1, new Date(NOW - 60_000).toISOString())])

    expect(playNewOrderOnceForOrders).toHaveBeenCalledTimes(1)
    expect(playNewOrderOnceForOrders.mock.calls[0][0]).toEqual([2])
  })

  it('does not ring for newly loaded old orders (pagination/filter/backfill)', async () => {
    await refreshHall([order(1, new Date(NOW - 60_000).toISOString())])
    playNewOrderOnceForOrders.mockClear()

    await refreshHall([
      order(1, new Date(NOW - 60_000).toISOString()),
      order(99, new Date(NOW - 3 * 60 * 60 * 1000).toISOString()),
    ])

    expect(playNewOrderOnceForOrders).not.toHaveBeenCalled()
  })

  it('does not ring on the initial load (no prior cache baseline)', async () => {
    await refreshHall([order(1)])
    expect(playNewOrderOnceForOrders).not.toHaveBeenCalled()
  })

  it('rings for the first order after an empty hall cache', async () => {
    await refreshHall([])
    expect(playNewOrderOnceForOrders).not.toHaveBeenCalled()

    await refreshHall([order(2)])

    expect(playNewOrderOnceForOrders).toHaveBeenCalledTimes(1)
    expect(playNewOrderOnceForOrders.mock.calls[0][0]).toEqual([2])
  })

  it('respects global DND / disabled new-order notifications', async () => {
    const settingsStore = useSettingsStore()
    settingsStore.preferences = {
      notification_settings: { global_dnd: true, SYSTEM_ANNOUNCEMENT: true },
    }
    await refreshHall([order(1, new Date(NOW - 60_000).toISOString())])
    await refreshHall([order(2), order(1, new Date(NOW - 60_000).toISOString())])
    expect(playNewOrderOnceForOrders).not.toHaveBeenCalled()

    settingsStore.preferences = {
      notification_settings: { global_dnd: false, SYSTEM_ANNOUNCEMENT: false },
    }
    await refreshHall([
      order(2),
      order(3),
      order(1, new Date(NOW - 60_000).toISOString()),
    ])
    expect(playNewOrderOnceForOrders).not.toHaveBeenCalled()
  })
})
