import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/api', () => ({ default: { get: vi.fn() } }))

import { useAdminOrderAnalytics, useMyOrderAnalytics } from '../useAdminOrderAnalytics'

const componentSource = readFileSync(new URL('../../OrderAnalyticsPanel.vue', import.meta.url), 'utf8')
const adminSource = readFileSync(new URL('../../../views/AdminView.vue', import.meta.url), 'utf8')
const adminWrapperSource = readFileSync(new URL('../AdminOrderAnalytics.vue', import.meta.url), 'utf8')
const myViewSource = readFileSync(new URL('../../../views/MyOrderAnalyticsView.vue', import.meta.url), 'utf8')
const routerSource = readFileSync(new URL('../../../router/index.js', import.meta.url), 'utf8')
const profileSource = readFileSync(new URL('../../../views/ProfileView.vue', import.meta.url), 'utf8')
const hallSource = readFileSync(new URL('../../../views/OrderHallView.vue', import.meta.url), 'utf8')

function apiWith(responses = {}) {
  return {
    get: vi.fn(async (path, options) => {
      if (responses.reject) throw responses.reject
      if (path.endsWith('/details')) return { data: responses.details || { items: [], page: 1, pages: 1, total: 0 } }
      return { data: responses.summary || { summary: {}, daily: [] } }
    }),
  }
}

describe('admin order analytics', () => {
  it('compiles and is available as an admin module with empty and error states', () => {
    const { descriptor, errors } = parse(componentSource, { filename: 'OrderAnalyticsPanel.vue' })
    expect(errors).toEqual([])
    expect(() => compileScript(descriptor, { id: 'OrderAnalyticsPanel.vue' })).not.toThrow()
    expect(componentSource).toContain('所选周期暂无发单数据')
    expect(componentSource).toContain('该日期没有符合条件的订单')
    expect(componentSource).toContain('重试')
    expect(componentSource).toContain('role="alert"')
    expect(adminSource).toContain("'order-analytics'")
    expect(adminSource).toContain('发单数据')
    expect(adminWrapperSource).toContain('scope="admin"')
    expect(myViewSource).toContain('scope="self"')
    expect(routerSource).toContain("path: '/my-order-analytics'")
    expect(routerSource).toContain("name: 'my-order-analytics'")
    expect(profileSource).toContain('user && user.role !== \'ADMIN\'')
    expect(profileSource).toContain('to="/my-order-analytics"')
    expect(profileSource).toContain("getUserRoleMeta(user.value?.role || 'USER')")
    expect(componentSource).toContain("issued_amount: '未取消发单金额'")
    expect(componentSource).toContain("issued: '订单金额'")
  })

  it('loads today summary and the selected period query', async () => {
    const client = apiWith({ summary: { summary: { issued_orders: 3 }, daily: [{ date: '2026-11-18', issued_orders: 3 }] } })
    const analytics = useAdminOrderAnalytics(client)

    await analytics.loadSummary()
    expect(client.get).toHaveBeenCalledWith('/admin/order-analytics', { params: { period: 'today' } })
    expect(analytics.summary.value.issued_orders).toBe(3)
    expect(analytics.daily.value).toHaveLength(1)
  })

  it('switches period, refreshes summary, and clears an old drill-down', async () => {
    const client = apiWith()
    const analytics = useAdminOrderAnalytics(client)
    analytics.selectedDate.value = '2026-11-18'
    analytics.selectedMetric.value = 'issued'
    analytics.details.value = [{ order_id: 5 }]

    await analytics.setPeriod('7d')
    expect(analytics.period.value).toBe('7d')
    expect(client.get).toHaveBeenCalledWith('/admin/order-analytics', { params: { period: '7d' } })
    expect(analytics.selectedDate.value).toBe('')
    expect(analytics.details.value).toEqual([])
  })

  it('requests order details for the selected day and metric', async () => {
    const client = apiWith({ details: { items: [{ order_id: 18 }], page: 1, pages: 2, total: 21 } })
    const analytics = useAdminOrderAnalytics(client)

    await analytics.openDetails('2026-11-17', 'cancelled')
    expect(client.get).toHaveBeenCalledWith('/admin/order-analytics/details', {
      params: {
        period: 'today',
        date: '2026-11-17',
        metric: 'cancelled',
        page: 1,
        page_size: 20,
      },
    })
    expect(analytics.details.value).toEqual([{ order_id: 18 }])
    expect(analytics.detailsTotal.value).toBe(21)
    expect(analytics.detailsPages.value).toBe(2)
  })

  it('loads the signed-in user analytics and lifetime totals from the self endpoint', async () => {
    const client = apiWith({
      summary: {
        summary: { issued_orders: 4, issued_amount: 520, successful_amount: 300 },
        daily: [],
        lifetime: { issued_orders: 18, issued_amount: 1600, successful_amount: 1150 },
      },
    })
    const analytics = useMyOrderAnalytics(client)

    await analytics.setPeriod('30d')
    expect(client.get).toHaveBeenCalledWith('/orders/my-analytics', { params: { period: '30d' } })
    expect(analytics.summary.value.issued_amount).toBe(520)
    expect(analytics.lifetime.value).toMatchObject({ issued_orders: 18, issued_amount: 1600, successful_amount: 1150 })
  })

  it('uses the separate issued_amount metric for non-cancelled order amount details', async () => {
    const client = apiWith({ details: { items: [], page: 1, pages: 1, total: 0 } })
    const analytics = useMyOrderAnalytics(client)

    await analytics.openDetails('2026-11-17', 'issued_amount')
    expect(client.get).toHaveBeenCalledWith('/orders/my-analytics/details', {
      params: {
        period: 'today',
        date: '2026-11-17',
        metric: 'issued_amount',
        page: 1,
        page_size: 20,
      },
    })
  })

  it('shows publisher nickname with the username fallback on order hall cards', () => {
    expect(hallSource).toContain('function getPublisherNickname(order)')
    expect(hallSource).toContain('order?.publisher_nickname || order?.user?.username')
    expect(hallSource).toContain('发单人 · {{ getPublisherNickname(order) }}')
    expect(hallSource).toContain('!bg-info-soft !text-info')
  })

  it('loads the next page and appends the order rows', async () => {
    const client = {
      get: vi.fn()
        .mockResolvedValueOnce({ data: { items: [{ order_id: 1 }], page: 1, pages: 2, total: 2 } })
        .mockResolvedValueOnce({ data: { items: [{ order_id: 2 }], page: 2, pages: 2, total: 2 } }),
    }
    const analytics = useAdminOrderAnalytics(client)

    await analytics.openDetails('2026-11-18', 'issued')
    await analytics.loadMoreDetails()

    expect(client.get.mock.calls[1][1].params.page).toBe(2)
    expect(analytics.details.value.map((item) => item.order_id)).toEqual([1, 2])
  })

  it('surfaces an API error and handles empty summary/detail responses', async () => {
    const failed = useAdminOrderAnalytics(apiWith({ reject: new Error('网络失败') }))
    await failed.loadSummary()
    expect(failed.error.value).toContain('网络失败')
    expect(failed.summary.value).toBeNull()

    const empty = useAdminOrderAnalytics(apiWith())
    await empty.loadSummary()
    await empty.openDetails('2026-11-18', 'issued')
    expect(empty.daily.value).toEqual([])
    expect(empty.details.value).toEqual([])
    expect(empty.detailsTotal.value).toBe(0)
  })

  it('surfaces a detail error and allows retrying the same date and metric', async () => {
    const client = {
      get: vi.fn()
        .mockRejectedValueOnce(new Error('明细暂不可用'))
        .mockResolvedValueOnce({ data: { items: [{ order_id: 8 }], page: 1, pages: 1, total: 1 } }),
    }
    const analytics = useAdminOrderAnalytics(client)

    await analytics.openDetails('2026-11-18', 'settled')
    expect(analytics.detailsError.value).toContain('明细暂不可用')

    await analytics.openDetails(analytics.selectedDate.value, analytics.selectedMetric.value)
    expect(client.get.mock.calls[1][1].params).toMatchObject({
      date: '2026-11-18',
      metric: 'settled',
      page: 1,
    })
    expect(analytics.detailsError.value).toBe('')
    expect(analytics.details.value).toEqual([{ order_id: 8 }])
  })
})
