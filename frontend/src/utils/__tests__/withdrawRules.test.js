import { describe, expect, it } from 'vitest'

import {
  REFRESH_MODE_META,
  WITHDRAWAL_INTERVAL_MAX_HOURS,
  WITHDRAWAL_INTERVAL_MIN_HOURS,
  WITHDRAWAL_MIN_AMOUNT,
  describeRefreshRule,
  findTodayWithdrawal,
  isWholeYuanAmount,
  quotaNotice,
  shanghaiDayKey,
  validateWithdrawAmount,
} from '../withdrawRules'

// 上海 2026-09-27 20:00（UTC+8）＝ UTC 12:00，跨时区用例统一锚到这个毫秒数
const SHANGHAI_0927_NOON_MS = Date.parse('2026-09-27T12:00:00Z')

describe('validateWithdrawAmount', () => {
  it('空值 / 非有限值提示先填写金额', () => {
    expect(validateWithdrawAmount('', 100)).toBe('请填写提现金额')
    expect(validateWithdrawAmount(null, 100)).toBe('请填写提现金额')
    expect(validateWithdrawAmount(undefined, 100)).toBe('请填写提现金额')
    expect(validateWithdrawAmount('abc', 100)).toBe('请填写提现金额')
    expect(validateWithdrawAmount(NaN, 100)).toBe('请填写提现金额')
  })

  it('低于 1 元被拒', () => {
    expect(validateWithdrawAmount('0', 100)).toBe('提现金额不能低于 1 元')
    expect(validateWithdrawAmount('0.99', 100)).toBe('提现金额不能低于 1 元')
    expect(validateWithdrawAmount(-5, 100)).toBe('提现金额不能低于 1 元')
  })

  it('小数（非整数元）被拒', () => {
    expect(validateWithdrawAmount('10.5', 100)).toBe('提现金额必须为整数（元）')
    expect(validateWithdrawAmount(10.01, 100)).toBe('提现金额必须为整数（元）')
  })

  it('超过可用余额被拒', () => {
    expect(validateWithdrawAmount('101', 100)).toBe('提现金额不能超过可用余额')
    expect(validateWithdrawAmount(101, 100)).toBe('提现金额不能超过可用余额')
  })

  it('合法整数通过；余额非有限数时只校验金额本身', () => {
    expect(validateWithdrawAmount('1', 100)).toBe('')
    expect(validateWithdrawAmount(100, 100)).toBe('')
    expect(validateWithdrawAmount('50', undefined)).toBe('')
    expect(validateWithdrawAmount('50', NaN)).toBe('')
    expect(validateWithdrawAmount('50', 'abc')).toBe('')
  })
})

describe('isWholeYuanAmount', () => {
  it('识别有限整数（含数字字符串）', () => {
    expect(isWholeYuanAmount(1)).toBe(true)
    expect(isWholeYuanAmount('100')).toBe(true)
    expect(isWholeYuanAmount(1.5)).toBe(false)
    expect(isWholeYuanAmount('1.5')).toBe(false)
    expect(isWholeYuanAmount(Infinity)).toBe(false)
    expect(isWholeYuanAmount('abc')).toBe(false)
  })
})

describe('提现常量', () => {
  it('最低 1 元、间隔 1-168 小时', () => {
    expect(WITHDRAWAL_MIN_AMOUNT).toBe(1)
    expect(WITHDRAWAL_INTERVAL_MIN_HOURS).toBe(1)
    expect(WITHDRAWAL_INTERVAL_MAX_HOURS).toBe(168)
  })
})

describe('describeRefreshRule', () => {
  it('模式 A：按间隔与「自上次提现起算」描述', () => {
    expect(REFRESH_MODE_META.INTERVAL.label).toBe('每隔 N 小时刷新')
    expect(describeRefreshRule({ mode: 'INTERVAL', interval_hours: 6 })).toBe(
      '每隔 6 小时刷新一次（按上次提现时间起算）'
    )
    expect(describeRefreshRule({ mode: 'INTERVAL', interval_hours: '24' })).toBe(
      '每隔 24 小时刷新一次（按上次提现时间起算）'
    )
  })

  it('模式 B：全站每天 12:00 刷新', () => {
    expect(REFRESH_MODE_META.DAILY_NOON.label).toBe('每天 12:00 刷新')
    expect(describeRefreshRule({ mode: 'DAILY_NOON', interval_hours: 6 })).toBe('每天 12:00 刷新一次')
  })

  it('规则为空或小时间隔非法时回落默认 24 小时', () => {
    expect(describeRefreshRule(null)).toBe('每隔 24 小时刷新一次')
    expect(describeRefreshRule({})).toBe('每隔 24 小时刷新一次')
    expect(describeRefreshRule({ mode: 'INTERVAL' })).toBe('每隔 24 小时刷新一次')
    expect(describeRefreshRule({ mode: 'INTERVAL', interval_hours: 0 })).toBe('每隔 24 小时刷新一次')
    expect(describeRefreshRule({ mode: 'INTERVAL', interval_hours: -3 })).toBe('每隔 24 小时刷新一次')
    expect(describeRefreshRule({ mode: 'INTERVAL', interval_hours: 2.5 })).toBe('每隔 24 小时刷新一次')
    expect(describeRefreshRule({ mode: 'INTERVAL', interval_hours: 'abc' })).toBe('每隔 24 小时刷新一次')
  })
})

describe('quotaNotice', () => {
  it('有机会：不拦截，文案带当前规则', () => {
    const notice = quotaNotice(
      { available: true, mode: 'INTERVAL', interval_hours: 24, next_refresh_at: null },
      SHANGHAI_0927_NOON_MS
    )
    expect(notice).toEqual({
      blocked: false,
      text: '当前有 1 次提现机会，每隔 24 小时刷新一次（按上次提现时间起算）',
    })
  })

  it('机会用完且下次刷新在未来：拦截，文案含恢复时间', () => {
    const notice = quotaNotice(
      { available: false, mode: 'INTERVAL', interval_hours: 24, next_refresh_at: '2026-09-28T04:00:00Z' },
      SHANGHAI_0927_NOON_MS
    )
    expect(notice.blocked).toBe(true)
    expect(notice.text).toContain('提现机会已用完')
    expect(notice.text).toContain('2026/09/28 12:00')
    expect(notice.text).toContain('后可再次申请')
    expect(notice.text).toContain('每隔 24 小时刷新一次（按上次提现时间起算）')
  })

  it('next_refresh_at 已过视为已恢复（无需刷新页面即可再提）', () => {
    const notice = quotaNotice(
      { available: false, mode: 'INTERVAL', interval_hours: 24, next_refresh_at: '2026-09-27T11:59:59Z' },
      SHANGHAI_0927_NOON_MS
    )
    expect(notice.blocked).toBe(false)
    expect(notice.text).toBe('当前有 1 次提现机会，每隔 24 小时刷新一次（按上次提现时间起算）')
  })

  it('next_refresh_at 解析不了时同样视为已恢复', () => {
    const notice = quotaNotice(
      { available: false, mode: 'INTERVAL', interval_hours: 24, next_refresh_at: 'not-a-date' },
      SHANGHAI_0927_NOON_MS
    )
    expect(notice.blocked).toBe(false)
    expect(notice.text).toContain('当前有 1 次提现机会')
  })

  it('模式 B 下 available=true 时 next_refresh_at 不能导致拦截', () => {
    const notice = quotaNotice(
      { available: true, mode: 'DAILY_NOON', interval_hours: 24, next_refresh_at: '2026-09-28T04:00:00Z' },
      SHANGHAI_0927_NOON_MS
    )
    expect(notice.blocked).toBe(false)
    expect(notice.text).toBe('当前有 1 次提现机会，每天 12:00 刷新一次')
  })

  it('模式 B 机会用完时展示下一轮刷新时间', () => {
    const notice = quotaNotice(
      { available: false, mode: 'DAILY_NOON', interval_hours: 24, next_refresh_at: '2026-09-28T04:00:00Z' },
      SHANGHAI_0927_NOON_MS
    )
    expect(notice.blocked).toBe(true)
    expect(notice.text).toContain('2026/09/28 12:00')
    expect(notice.text).toContain('每天 12:00 刷新一次')
  })

  it('quota 为空（未加载）时不展示任何提示', () => {
    expect(quotaNotice(null, SHANGHAI_0927_NOON_MS)).toEqual({ blocked: false, text: '' })
    expect(quotaNotice(undefined, SHANGHAI_0927_NOON_MS)).toEqual({ blocked: false, text: '' })
  })
})

describe('shanghaiDayKey', () => {
  it('按 Asia/Shanghai 归日，不依赖 locale 输出格式', () => {
    expect(shanghaiDayKey('2026-09-26T16:30:00Z')).toBe('2026-09-27')
    expect(shanghaiDayKey('2026-09-26T15:59:59Z')).toBe('2026-09-26')
    expect(shanghaiDayKey('2026-09-27T12:00:00Z')).toBe('2026-09-27')
  })

  it('无时区后缀按上海墙钟时间解释（同后端 DATETIME 约定）', () => {
    expect(shanghaiDayKey('2026-09-27 10:00:00')).toBe('2026-09-27')
    expect(shanghaiDayKey('2026-09-27 00:30:00')).toBe('2026-09-27')
  })

  it('非法值返回空串', () => {
    expect(shanghaiDayKey(null)).toBe('')
    expect(shanghaiDayKey('')).toBe('')
    expect(shanghaiDayKey('garbage')).toBe('')
  })
})

describe('findTodayWithdrawal', () => {
  const now = Date.parse('2026-09-27T12:00:00Z') // 上海 2026-09-27 20:00

  it('返回当天第一条未驳回提现', () => {
    const withdrawals = [
      { id: 1, status: 'PENDING', created_at: '2026-09-25T20:00:00Z' }, // 上海 09-26 04:00，昨天
      { id: 2, status: 'PENDING', created_at: '2026-09-27T00:00:00Z' }, // 上海 09-27 08:00，今天
      { id: 3, status: 'REJECTED', created_at: '2026-09-27T04:00:00Z' }, // 上海 09-27 12:00，今天但被驳回
    ]
    expect(findTodayWithdrawal(withdrawals, now)?.id).toBe(2)
  })

  it('跳过当天排在前面的被驳回记录', () => {
    const withdrawals = [
      { id: 5, status: 'REJECTED', created_at: '2026-09-27T00:00:00Z' },
      { id: 6, status: 'APPROVED', created_at: '2026-09-27T04:00:00Z' },
    ]
    expect(findTodayWithdrawal(withdrawals, now)?.id).toBe(6)
  })

  it('跳过 REJECTED：当天只有被驳回记录时返回 null', () => {
    const withdrawals = [{ id: 9, status: 'REJECTED', created_at: '2026-09-27T04:00:00Z' }]
    expect(findTodayWithdrawal(withdrawals, now)).toBeNull()
  })

  it('没有当天记录时返回 null', () => {
    const withdrawals = [{ id: 8, status: 'PENDING', created_at: '2026-09-20T04:00:00Z' }]
    expect(findTodayWithdrawal(withdrawals, now)).toBeNull()
  })

  it('入参非数组时返回 null', () => {
    expect(findTodayWithdrawal(null, now)).toBeNull()
    expect(findTodayWithdrawal(undefined, now)).toBeNull()
  })
})
