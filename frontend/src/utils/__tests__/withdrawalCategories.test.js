import { describe, expect, it } from 'vitest'

import {
  PAYOUT_CATEGORIES,
  PAYOUT_REMARK_PREFIX,
  PAYOUT_RESULT_META,
  RECEIPT_STATUS,
  buildPayoutRemark,
  defaultBatchRemark,
  getPayoutCategoryMeta,
  matchReceiptRows,
  parsePayoutRemark,
  parseReceiptRow,
  summarizeImport,
} from '../withdrawalCategories'

describe('PAYOUT_CATEGORIES', () => {
  it('只含支付宝/微信两个打款渠道，且带 label 与渠道', () => {
    expect(PAYOUT_CATEGORIES).toEqual([
      { value: 'ALIPAY', label: '支付宝', channel: 'ALIPAY' },
      { value: 'WECHAT', label: '微信', channel: 'WECHAT' },
    ])
  })

  it('getPayoutCategoryMeta 命中返回 meta，未知值返回 undefined', () => {
    expect(getPayoutCategoryMeta('ALIPAY')).toEqual({ value: 'ALIPAY', label: '支付宝', channel: 'ALIPAY' })
    expect(getPayoutCategoryMeta('WECHAT').label).toBe('微信')
    expect(getPayoutCategoryMeta('BANK')).toBeUndefined()
    expect(getPayoutCategoryMeta('')).toBeUndefined()
    expect(getPayoutCategoryMeta(null)).toBeUndefined()
  })
})

describe('buildPayoutRemark / parsePayoutRemark', () => {
  it('前缀常量固定', () => {
    expect(PAYOUT_REMARK_PREFIX).toBe('GW提现单W')
  })

  it('正常 id 可往返', () => {
    expect(buildPayoutRemark(10086)).toBe('GW提现单W10086')
    expect(parsePayoutRemark(buildPayoutRemark(10086))).toBe(10086)
    expect(parsePayoutRemark('GW提现单W1')).toBe(1)
  })

  it('非正/非整数/非数字 id 建不出备注', () => {
    expect(buildPayoutRemark(0)).toBe('')
    expect(buildPayoutRemark(-5)).toBe('')
    expect(buildPayoutRemark(1.5)).toBe('')
    expect(buildPayoutRemark('abc')).toBe('')
    expect(buildPayoutRemark(NaN)).toBe('')
    expect(buildPayoutRemark(null)).toBe('')
    expect(buildPayoutRemark(undefined)).toBe('')
    expect(buildPayoutRemark('')).toBe('')
  })

  it('夹在更长备注里也能取到 id', () => {
    expect(parsePayoutRemark('批量付款-9.28号提现打款 GW提现单W10086')).toBe(10086)
    expect(parsePayoutRemark('GW提现单W10086 转账')).toBe(10086)
    expect(parsePayoutRemark('前缀 GW提现单W42 后缀')).toBe(42)
  })

  it('脏备注返回 null', () => {
    expect(parsePayoutRemark('')).toBeNull()
    expect(parsePayoutRemark('W10086')).toBeNull()
    expect(parsePayoutRemark('GW提现单Wabc')).toBeNull()
    expect(parsePayoutRemark('GW提现单W')).toBeNull()
    expect(parsePayoutRemark(null)).toBeNull()
    expect(parsePayoutRemark(undefined)).toBeNull()
    expect(parsePayoutRemark(10086)).toBeNull()
    expect(parsePayoutRemark({})).toBeNull()
  })
})

describe('RECEIPT_STATUS', () => {
  it('只认成功/失败两个口径', () => {
    expect(RECEIPT_STATUS).toEqual({ 成功: 'SUCCESS', 失败: 'FAIL' })
  })
})

describe('parseReceiptRow', () => {
  const fullRow = [
    '1',
    '2026-09-28 10:00:00',
    'DD20260928001',
    '2026092812345678',
    '138****8888',
    '张三',
    '50.00',
    '成功',
    'GW提现单W1',
    '',
  ]

  it('按回执明细表列序解析整行', () => {
    expect(parseReceiptRow(fullRow, 0)).toEqual({
      seq: 1,
      created_at: '2026-09-28 10:00:00',
      order_no: 'DD20260928001',
      serial_no: '2026092812345678',
      account_no: '138****8888',
      account_name: '张三',
      amount: 50,
      status: 'SUCCESS',
      status_raw: '成功',
      remark: 'GW提现单W1',
      fail_reason: '',
    })
  })

  it('千分位金额去掉逗号', () => {
    expect(parseReceiptRow([2, '', '', '', 'a@b.com', '李四', '1,234.50', '成功', '', ''], 1).amount).toBe(1234.5)
  })

  it('未知状态映射为 UNKNOWN，不当成功处理', () => {
    const row = parseReceiptRow([3, '', '', '', '138****8888', '王五', '10.00', '处理中', '', ''], 2)
    expect(row.status).toBe('UNKNOWN')
    expect(row.status_raw).toBe('处理中')
    expect(parseReceiptRow(['x', '', '', '', '138****8888', '王五', '10.00', '成功', '', '']).seq).toBeNull()
  })

  it('金额解析不了返回 null', () => {
    expect(parseReceiptRow([4, '', '', '', '138****8888', '赵六', '--', '成功', '', '']).amount).toBeNull()
    expect(parseReceiptRow([4, '', '', '', '138****8888', '赵六', '', '成功', '', '']).amount).toBeNull()
    expect(parseReceiptRow([4, '', '', '', '138****8888', '赵六', 'abc', '成功', '', '']).amount).toBeNull()
  })

  it('短行/非数组按空值处理，序号脏值返回 null', () => {
    const short = parseReceiptRow(['5', '2026-09-28 09:00:00'], 3)
    expect(short.seq).toBe(5)
    expect(short.order_no).toBe('')
    expect(short.amount).toBeNull()
    expect(short.fail_reason).toBe('')

    expect(parseReceiptRow(null, 0)).toEqual({
      seq: null,
      created_at: '',
      order_no: '',
      serial_no: '',
      account_no: '',
      account_name: '',
      amount: null,
      status: 'UNKNOWN',
      status_raw: '',
      remark: '',
      fail_reason: '',
    })
    expect(parseReceiptRow(undefined, 0).seq).toBeNull()
    expect(parseReceiptRow('-', 0).seq).toBeNull()
    expect(parseReceiptRow('abc', 0).seq).toBeNull()
  })
})

describe('matchReceiptRows', () => {
  const items = [
    { id: 1, account_no: '138****8888', account_name: '张三', amount: 50, status: 'PENDING' },
    { id: 2, account_no: '139****9999', account_name: '李四', amount: 80, status: 'APPROVED' },
  ]

  it('序号优先于备注：两线索冲突时按提现单 id 命中', () => {
    const rows = [parseReceiptRow(['1', '', '', '', '139****9999', '李四', '80.00', '成功', 'GW提现单W2', ''])]
    const [match] = matchReceiptRows(rows, items)
    expect(match.item.id).toBe(1)
    expect(match.matched_by).toBe('seq')
    expect(match.result).toBe('PAID')
  })

  it('序号缺失或脏值时回落备注里的 GW提现单W{id}', () => {
    const rows = [
      parseReceiptRow(['', '', '', '', 'no-match@x.com', '无人', '10.00', '成功', 'GW提现单W2', '']),
      parseReceiptRow(['-', '', '', '', 'no-match@x.com', '无人', '10.00', '失败', 'GW提现单W1', '余额不足']),
    ]
    const [byRemark, rejected] = matchReceiptRows(rows, items)
    expect(byRemark.item.id).toBe(2)
    expect(byRemark.matched_by).toBe('remark')
    expect(byRemark.result).toBe('PAID')
    expect(rejected.item.id).toBe(1)
    expect(rejected.matched_by).toBe('remark')
    expect(rejected.result).toBe('REJECTED')
  })

  it('序号与备注都无效时按账号+姓名+金额匹配', () => {
    const rows = [parseReceiptRow(['', '', '', '', '138****8888', '张三', '50.00', '成功', '随便写的备注', ''])]
    const [match] = matchReceiptRows(rows, items)
    expect(match.item.id).toBe(1)
    expect(match.matched_by).toBe('account')
    expect(match.result).toBe('PAID')
  })

  it('金额差在 0.005 内仍算命中', () => {
    const rows = [parseReceiptRow(['', '', '', '', '139****9999', '李四', '79.998', '成功', '', ''])]
    const [match] = matchReceiptRows(rows, items)
    expect(match.item.id).toBe(2)
    expect(match.matched_by).toBe('account')
  })

  it('同一提现单不会被两行重复消耗', () => {
    const rows = [
      parseReceiptRow(['', '', '', '', '138****8888', '张三', '50.00', '成功', '', '']),
      parseReceiptRow(['', '', '', '', '138****8888', '张三', '50.00', '成功', '', '']),
    ]
    const [first, second] = matchReceiptRows(rows, items)
    expect(first.item?.id).toBe(1)
    expect(first.result).toBe('PAID')
    expect(second.item).toBeNull()
    expect(second.matched_by).toBeNull()
    expect(second.result).toBe('UNMATCHED')
  })

  it('账号+姓名+金额圈出多笔时不认领', () => {
    const duplicated = [
      { id: 7, account_no: '138****8888', account_name: '张三', amount: 50, status: 'PENDING' },
      { id: 8, account_no: '138****8888', account_name: '张三', amount: 50, status: 'PENDING' },
    ]
    const rows = [parseReceiptRow(['', '', '', '', '138****8888', '张三', '50.00', '成功', '', ''])]
    const [match] = matchReceiptRows(rows, duplicated)
    expect(match.item).toBeNull()
    expect(match.matched_by).toBeNull()
    expect(match.result).toBe('UNMATCHED')
  })

  it('已打款/已驳回等终态提现单不可匹配', () => {
    const terminal = [
      { id: 11, account_no: '138****8888', account_name: '张三', amount: 50, status: 'PAID' },
      { id: 12, account_no: '139****9999', account_name: '李四', amount: 80, status: 'REJECTED' },
    ]
    const rows = [
      parseReceiptRow(['11', '', '', '', '138****8888', '张三', '50.00', '成功', '', '']),
      parseReceiptRow(['', '', '', '', '139****9999', '李四', '80.00', '成功', 'GW提现单W12', '']),
      parseReceiptRow(['', '', '', '', '139****9999', '李四', '80.00', '成功', '', '']),
    ]
    const matches = matchReceiptRows(rows, terminal)
    expect(matches.map((entry) => entry.result)).toEqual(['UNMATCHED', 'UNMATCHED', 'UNMATCHED'])
    expect(matches.every((entry) => entry.item === null)).toBe(true)
    expect(matches.every((entry) => entry.matched_by === null)).toBe(true)
  })

  it('成功但没匹配上 = UNMATCHED；状态未知即便匹配上也只算未匹配', () => {
    const unknownRow = parseReceiptRow(['1', '', '', '', '138****8888', '张三', '50.00', '处理中', '', ''])
    const noMatchRow = parseReceiptRow(['999', '', '', '', '137****0000', '无人', '1.00', '成功', '', ''])
    const [unknown, noMatch] = matchReceiptRows([unknownRow, noMatchRow], items)
    expect(unknown.item?.id).toBe(1)
    expect(unknown.result).toBe('UNMATCHED')
    expect(noMatch.item).toBeNull()
    expect(noMatch.result).toBe('UNMATCHED')
  })

  it('入参非数组不炸', () => {
    expect(matchReceiptRows(null, null)).toEqual([])
    expect(matchReceiptRows(undefined, items)).toEqual([])
    expect(matchReceiptRows([], undefined)).toEqual([])
  })
})

describe('summarizeImport', () => {
  it('按 PAID/REJECTED/UNMATCHED 计数，未知状态计入未匹配', () => {
    const rows = [
      parseReceiptRow(['1', '', '', '', '138****8888', '张三', '50.00', '成功', '', '']),
      parseReceiptRow(['2', '', '', '', '139****9999', '李四', '80.00', '失败', '', '余额不足']),
      parseReceiptRow(['999', '', '', '', '137****0000', '无人', '1.00', '成功', '', '']),
      parseReceiptRow(['1', '', '', '', '138****8888', '张三', '50.00', '处理中', '', '']),
    ]
    const items = [
      { id: 1, account_no: '138****8888', account_name: '张三', amount: 50, status: 'PENDING' },
      { id: 2, account_no: '139****9999', account_name: '李四', amount: 80, status: 'APPROVED' },
    ]
    expect(summarizeImport(matchReceiptRows(rows, items))).toEqual({
      success: 1,
      failed: 1,
      unmatched: 2,
    })
  })

  it('空列表 / 非数组全为 0', () => {
    expect(summarizeImport([])).toEqual({ success: 0, failed: 0, unmatched: 0 })
    expect(summarizeImport(null)).toEqual({ success: 0, failed: 0, unmatched: 0 })
  })
})

describe('defaultBatchRemark', () => {
  it('按上海墙钟日期生成备注（月份日期不补零）', () => {
    expect(defaultBatchRemark(new Date('2026-09-27T16:30:00Z'))).toBe('9月28号提现打款')
    expect(defaultBatchRemark(new Date('2026-09-27T15:59:59Z'))).toBe('9月27号提现打款')
    expect(defaultBatchRemark(new Date('2026-09-27T16:00:00Z'))).toBe('9月28号提现打款')
    expect(defaultBatchRemark(new Date('2026-01-01T00:30:00Z'))).toBe('1月1号提现打款')
    expect(defaultBatchRemark(new Date('2026-12-31T16:30:00Z'))).toBe('1月1号提现打款')
  })

  it('非法时间回落当前时间', () => {
    expect(defaultBatchRemark(new Date('not-a-date'))).toMatch(/^\d{1,2}月\d{1,2}号提现打款$/)
    expect(defaultBatchRemark(null)).toMatch(/^\d{1,2}月\d{1,2}号提现打款$/)
  })
})

describe('PAYOUT_RESULT_META', () => {
  it('四种导入结果都有中文标签', () => {
    expect(PAYOUT_RESULT_META).toEqual({
      PAID: { label: '打款成功' },
      REJECTED: { label: '已驳回' },
      SKIPPED: { label: '已跳过' },
      UNMATCHED: { label: '未匹配' },
    })
  })
})
