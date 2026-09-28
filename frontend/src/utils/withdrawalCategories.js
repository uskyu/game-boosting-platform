// 提现打款批次的纯函数：批次备注协议、回执表解析与匹配、批次备注默认值。
// 不依赖 store / axios / DOM，可在 node 环境直接单测。

export const PAYOUT_CATEGORIES = [
  { value: 'ALIPAY', label: '支付宝', channel: 'ALIPAY' },
  { value: 'WECHAT', label: '微信', channel: 'WECHAT' },
]

export function getPayoutCategoryMeta(value) {
  return PAYOUT_CATEGORIES.find((item) => item.value === value)
}

// 打款备注协议：把提现单 id 夹在付款备注里带给银行，回执回来再靠它反查原单
export const PAYOUT_REMARK_PREFIX = 'GW提现单W'
const PAYOUT_REMARK_PATTERN = new RegExp(`${PAYOUT_REMARK_PREFIX}(\\d+)`)

export function buildPayoutRemark(withdrawalId) {
  const id = Number(withdrawalId)
  if (!Number.isInteger(id) || id <= 0) {
    return ''
  }
  return `${PAYOUT_REMARK_PREFIX}${id}`
}

// 备注前后可能带运营自己写的说明（如「批量付款-9.28号提现打款 GW提现单W10086」），
// 所以只在前缀后抓数字，不要求整串相等
export function parsePayoutRemark(remark) {
  if (typeof remark !== 'string') {
    return null
  }
  const matched = remark.match(PAYOUT_REMARK_PATTERN)
  if (!matched) {
    return null
  }
  const id = Number(matched[1])
  return Number.isSafeInteger(id) && id > 0 ? id : null
}

// 回执明细表「状态」列中文口径：只认成功/失败，其余一律 UNKNOWN（上游按未匹配处理）
export const RECEIPT_STATUS = {
  成功: 'SUCCESS',
  失败: 'FAIL',
}

// 回执明细表列序：序号 / 创建时间 / 订单号 / 流水号 / 收款账号 / 姓名 / 金额 / 状态 / 备注 / 失败原因
function receiptCell(rawRow, index) {
  if (!Array.isArray(rawRow) || index < 0 || index >= rawRow.length) {
    return ''
  }
  const value = rawRow[index]
  return value === null || value === undefined ? '' : String(value).trim()
}

function parseReceiptSeq(raw) {
  // Excel 里序号可能是 1 / "1" / "1.0"，脏值（"-" / "abc" / 空）一律放弃该线索
  const matched = String(raw ?? '').trim().match(/^(\d+)(?:\.0+)?$/)
  if (!matched) {
    return null
  }
  const seq = Number(matched[1])
  return Number.isSafeInteger(seq) && seq > 0 ? seq : null
}

// 金额按字符串解析：去掉千分位逗号，解析不了返回 null（宁可不匹配也不错认）
function parseReceiptAmount(raw) {
  const text = String(raw ?? '').trim().replace(/,/g, '')
  if (!text) {
    return null
  }
  const amount = Number(text)
  return Number.isFinite(amount) ? amount : null
}

// 用 hasOwnProperty 取映射，避免 'constructor' 之类的键穿透到原型链
function toReceiptStatus(raw) {
  return Object.prototype.hasOwnProperty.call(RECEIPT_STATUS, raw) ? RECEIPT_STATUS[raw] : 'UNKNOWN'
}

// index 只是调用方（表格渲染 / 日志）定位原始行用，解析结果不依赖它
export function parseReceiptRow(rawRow, index) {
  const statusRaw = receiptCell(rawRow, 7)
  return {
    seq: parseReceiptSeq(receiptCell(rawRow, 0)),
    created_at: receiptCell(rawRow, 1),
    order_no: receiptCell(rawRow, 2),
    serial_no: receiptCell(rawRow, 3),
    account_no: receiptCell(rawRow, 4),
    account_name: receiptCell(rawRow, 5),
    amount: parseReceiptAmount(receiptCell(rawRow, 6)),
    status: toReceiptStatus(statusRaw),
    status_raw: statusRaw,
    remark: receiptCell(rawRow, 8),
    fail_reason: receiptCell(rawRow, 9),
  }
}

// 只有还在审核链上的提现单才允许被打款/驳回：已打款、已驳回的不能重复消耗
const MATCHABLE_ITEM_STATUSES = ['PENDING', 'APPROVED']
// 回执金额与提现金额按分位比较时的容差（浮点误差）
const AMOUNT_TOLERANCE = 0.005

function isMatchableItem(item) {
  return MATCHABLE_ITEM_STATUSES.includes(String(item?.status ?? '').toUpperCase())
}

function sameText(left, right) {
  const a = left == null ? '' : String(left).trim()
  const b = right == null ? '' : String(right).trim()
  // 空值不参与账号匹配：两行都空账号时不能互相认领
  return a !== '' && a === b
}

function sameItemId(itemId, targetId) {
  const id = Number(itemId)
  return Number.isFinite(id) && id === targetId
}

function pickById(pool, consumed, id) {
  if (id == null) {
    return null
  }
  return (
    pool.find(
      (item) => !consumed.has(item) && isMatchableItem(item) && sameItemId(item?.id, id)
    ) || null
  )
}

function pickByAccount(pool, consumed, row) {
  const amount = Number(row?.amount)
  if (!Number.isFinite(amount)) {
    return null
  }
  const candidates = pool.filter(
    (item) =>
      !consumed.has(item) &&
      isMatchableItem(item) &&
      sameText(item?.account_no, row?.account_no) &&
      sameText(item?.account_name, row?.account_name) &&
      Math.abs(Number(item?.amount) - amount) <= AMOUNT_TOLERANCE
  )
  // 账号+姓名+金额能圈出多笔时不敢认领，留给人工核对
  return candidates.length === 1 ? candidates[0] : null
}

function resolveMatchResult(status, matchedItem) {
  if (!matchedItem) {
    return 'UNMATCHED'
  }
  if (status === 'SUCCESS') {
    return 'PAID'
  }
  if (status === 'FAIL') {
    return 'REJECTED'
  }
  // UNKNOWN 状态即便有匹配项也不算成功，只作未匹配
  return 'UNMATCHED'
}

// 回执行与提现单匹配：先序号（提现单 id），再备注里的 GW提现单W{id}，
// 最后账号+姓名+金额唯一命中；同一提现单最多被一行消耗。
export function matchReceiptRows(receiptRows, items) {
  const rows = Array.isArray(receiptRows) ? receiptRows : []
  const pool = Array.isArray(items) ? items : []
  const consumed = new Set()

  return rows.map((row) => {
    let matchedItem = pickById(pool, consumed, row?.seq)
    let matchedBy = matchedItem ? 'seq' : null

    if (!matchedItem) {
      const remarkId = parsePayoutRemark(row?.remark)
      matchedItem = pickById(pool, consumed, remarkId)
      if (matchedItem) {
        matchedBy = 'remark'
      }
    }

    if (!matchedItem) {
      matchedItem = pickByAccount(pool, consumed, row)
      if (matchedItem) {
        matchedBy = 'account'
      }
    }

    if (matchedItem) {
      consumed.add(matchedItem)
    }

    return {
      row,
      item: matchedItem || null,
      matched_by: matchedBy,
      result: resolveMatchResult(row?.status, matchedItem),
    }
  })
}

export function summarizeImport(matches) {
  const list = Array.isArray(matches) ? matches : []
  const count = (result) => list.filter((entry) => entry?.result === result).length
  return {
    success: count('PAID'),
    failed: count('REJECTED'),
    // UNKNOWN 状态的行也计入未匹配，绝不混进成功
    unmatched: count('UNMATCHED'),
  }
}

let monthDayFormatter = null

// 按 Asia/Shanghai 墙钟取月/日，不依赖本机时区；老运行时应不到 Intl 时区数据
// （或 formatToParts 输出异常）时手算 UTC+8
function shanghaiMonthDay(date) {
  try {
    if (!monthDayFormatter) {
      monthDayFormatter = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Shanghai',
        month: 'numeric',
        day: 'numeric',
      })
    }
    const parts = monthDayFormatter.formatToParts(date)
    const pick = (type) => Number(parts.find((part) => part.type === type)?.value)
    const month = pick('month')
    const day = pick('day')
    if (Number.isInteger(month) && month >= 1 && month <= 12 && Number.isInteger(day) && day >= 1 && day <= 31) {
      return { month, day }
    }
  } catch {
    // 落到下面的手工兜底
  }
  const shifted = new Date(date.getTime() + 8 * 60 * 60 * 1000)
  return { month: shifted.getUTCMonth() + 1, day: shifted.getUTCDate() }
}

// 新建批次备注默认值：按上海自然日，不补零（9月8号）
export function defaultBatchRemark(now = new Date()) {
  const date = now instanceof Date ? now : new Date(now)
  const target = Number.isNaN(date.getTime()) ? new Date() : date
  const { month, day } = shanghaiMonthDay(target)
  return `${month}月${day}号提现打款`
}

// 导入结果的行级状态展示
export const PAYOUT_RESULT_META = {
  PAID: { label: '打款成功' },
  REJECTED: { label: '已驳回' },
  SKIPPED: { label: '已跳过' },
  UNMATCHED: { label: '未匹配' },
}
