/**
 * 紧凑分页的页码序列。
 *
 * 大页码时绝不把全部页码渲染出来，否则 flex 行会被撑到几千像素。
 * 规则：
 * - 总页数 <= 7：全部显示；
 * - 否则始终保留首尾页、当前页及其左右邻页；
 * - 靠近首部显示 1..5，靠近尾部显示 last-4..last；
 * - 缺口大于 1 页才插入一个省略号，最多 7 项且无重复。
 */
export function buildPageItems(current, totalPages) {
  const toInteger = (value, fallback) => {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) return fallback
    return Math.trunc(numeric)
  }

  const total = Math.max(1, toInteger(totalPages, 1))
  const active = Math.min(total, Math.max(1, toInteger(current, 1)))

  if (total <= 1) return [1]
  if (total <= 7) {
    return Array.from({ length: total }, (_, index) => index + 1)
  }

  // 首部窗口：1..5 + 末页。
  if (active <= 4) {
    return [1, 2, 3, 4, 5, 'ellipsis', total]
  }

  // 尾部窗口：首页 + last-4..last。
  if (active >= total - 3) {
    const start = total - 4
    return [1, 'ellipsis', start, start + 1, start + 2, start + 3, total]
  }

  // 中间页：首页 + current-1/current/current+1 + 末页。
  return [1, 'ellipsis', active - 1, active, active + 1, 'ellipsis', total]
}
