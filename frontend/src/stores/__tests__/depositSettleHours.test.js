import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { createPinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: vi.fn(),
    put: vi.fn(),
  },
}))

import { useWalletStore } from '@/stores/wallet'

beforeEach(() => {
  apiGet.mockReset()
  setActivePinia(createPinia())
})

describe('保证金概览的 settle_hours 归一化', () => {
  it('留空（null）必须原样保留，不能兜底成 0', async () => {
    // 0 = 立即结算，null = 按订单到账时效；两者语义相反。
    // 旧实现用 toNumber(x, 0) 把 null 变成 0，页面把「按订单时效」错显成
    // 「立即结算」（老板 2026-10-18 报的 bug）。
    apiGet.mockResolvedValue({
      data: {
        enabled: true,
        deposit_balance: '10.00',
        available_balance: '20.00',
        current_threshold: '0.00',
        wait_seconds: 30,
        exempt_compensation: true,
        settle_hours: null,
        tiers: [
          { id: 1, threshold: '0.00', wait_seconds: 30, settle_hours: null, enabled: true },
          { id: 2, threshold: '100.00', wait_seconds: 32, settle_hours: 72, enabled: true },
        ],
      },
    })

    const store = useWalletStore()
    await store.fetchDepositOverview()

    expect(store.depositOverview.settle_hours).toBeNull()
    expect(store.depositOverview.tiers[0].settle_hours).toBeNull()
    // 有值的档位仍要正常转成数字
    expect(store.depositOverview.tiers[1].settle_hours).toBe(72)
  })

  it('显式 0 小时仍保留为 0（= 立即结算），不被当成留空', async () => {
    apiGet.mockResolvedValue({
      data: {
        enabled: true,
        settle_hours: 0,
        tiers: [{ id: 1, threshold: '100.00', settle_hours: 0, enabled: true }],
      },
    })

    const store = useWalletStore()
    await store.fetchDepositOverview()

    expect(store.depositOverview.settle_hours).toBe(0)
    expect(store.depositOverview.tiers[0].settle_hours).toBe(0)
  })

  it('字符串数字仍转成 Number（后端可能以字符串返回）', async () => {
    apiGet.mockResolvedValue({
      data: { enabled: true, settle_hours: '48', tiers: [] },
    })

    const store = useWalletStore()
    await store.fetchDepositOverview()

    expect(store.depositOverview.settle_hours).toBe(48)
  })
})

describe('后台保证金设置：不得重新引入「必须含 0 元档」校验', () => {
  const source = readFileSync(
    new URL('../../components/admin/AdminDepositSettings.vue', import.meta.url),
    'utf8',
  )

  it('保存前不再拦截缺少 0 元档的阶梯', () => {
    // 老板有意删掉 0 元档好让 0 保证金打手能接单；该校验会把后台保存卡死
    // （后端 2026-10-18 已撤除，前端必须同步）。
    expect(source).not.toContain('保证金阶梯必须包含一条启用中的 0 元档')
    expect(source).not.toContain('zeroEnabled')
  })

  it('结账时效留空仍发 null（不代填 0）', () => {
    expect(source).toMatch(/settle_hours === '' \|\| tier\.settle_hours == null/)
  })
})
