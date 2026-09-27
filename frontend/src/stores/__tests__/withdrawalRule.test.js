import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPut = vi.fn()
vi.mock('@/utils/api', () => ({
  default: { get: (...args) => apiGet(...args), put: (...args) => apiPut(...args) },
}))

import { useWalletStore } from '@/stores/wallet'
import { useWithdrawalRuleStore } from '@/stores/withdrawalRule'

let pinia

beforeEach(() => {
  apiGet.mockReset()
  apiPut.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

describe('withdrawalRule store', () => {
  it('fetchSettings 拉到设置并写入 state', async () => {
    apiGet.mockResolvedValue({ data: { mode: 'INTERVAL', interval_hours: 6, updated_at: '2026-09-27T04:00:00Z' } })
    const store = useWithdrawalRuleStore()

    const result = await store.fetchSettings()

    expect(apiGet).toHaveBeenCalledWith('/admin/withdrawal-rule/settings')
    expect(result).toEqual({
      success: true,
      data: { mode: 'INTERVAL', interval_hours: 6, updated_at: '2026-09-27T04:00:00Z' },
    })
    expect(store.settings).toEqual({ mode: 'INTERVAL', interval_hours: 6, updated_at: '2026-09-27T04:00:00Z' })
    expect(store.loading).toBe(false)
  })

  it('有缓存且非 force 时不再请求', async () => {
    apiGet.mockResolvedValue({ data: { mode: 'DAILY_NOON', interval_hours: 24, updated_at: null } })
    const store = useWithdrawalRuleStore()

    await store.fetchSettings()
    const result = await store.fetchSettings()

    expect(apiGet).toHaveBeenCalledTimes(1)
    expect(result.success).toBe(true)
  })

  it('force 时强制重新请求', async () => {
    apiGet.mockResolvedValue({ data: { mode: 'INTERVAL', interval_hours: 6, updated_at: null } })
    const store = useWithdrawalRuleStore()

    await store.fetchSettings()
    apiGet.mockResolvedValue({ data: { mode: 'DAILY_NOON', interval_hours: 24, updated_at: null } })
    const result = await store.fetchSettings(true)

    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(result.data.mode).toBe('DAILY_NOON')
  })

  it('updateSettings 提交 mode 与 interval_hours 并写回 state', async () => {
    apiPut.mockResolvedValue({ data: { mode: 'DAILY_NOON', interval_hours: 12, updated_at: '2026-09-27T05:00:00Z' } })
    const store = useWithdrawalRuleStore()

    const result = await store.updateSettings({ mode: 'DAILY_NOON', interval_hours: 12 })

    expect(apiPut).toHaveBeenCalledWith('/admin/withdrawal-rule/settings', { mode: 'DAILY_NOON', interval_hours: 12 })
    expect(result.success).toBe(true)
    expect(store.settings).toEqual({ mode: 'DAILY_NOON', interval_hours: 12, updated_at: '2026-09-27T05:00:00Z' })
  })

  it('请求失败返回 { success: false, error } 且不抛', async () => {
    apiGet.mockRejectedValue({ message: '仅管理员可操作' })
    apiPut.mockRejectedValue({ message: '间隔小时数需为 1-168' })
    const store = useWithdrawalRuleStore()

    const fetched = await store.fetchSettings()
    expect(fetched).toEqual({ success: false, error: '仅管理员可操作' })
    expect(store.settings).toBeNull()
    expect(store.loading).toBe(false)

    const updated = await store.updateSettings({ mode: 'INTERVAL', interval_hours: 999 })
    expect(updated).toEqual({ success: false, error: '间隔小时数需为 1-168' })
  })
})

describe('wallet store fetchQuota', () => {
  it('成功时写入 quota', async () => {
    const quota = { available: false, mode: 'DAILY_NOON', interval_hours: 24, next_refresh_at: '2026-09-28T04:00:00Z' }
    apiGet.mockResolvedValue({ data: quota })
    const wallet = useWalletStore()

    const result = await wallet.fetchQuota()

    expect(apiGet).toHaveBeenCalledWith('/withdrawals/quota')
    expect(result).toEqual({ success: true, data: quota })
    expect(wallet.quota).toEqual(quota)
  })

  it('失败返回错误且保持 quota 为 null', async () => {
    apiGet.mockRejectedValue({ message: '请先登录' })
    const wallet = useWalletStore()

    const result = await wallet.fetchQuota()

    expect(result).toEqual({ success: false, error: '请先登录' })
    expect(wallet.quota).toBeNull()
  })
})

afterEach(() => {
  disposePinia(pinia)
})
