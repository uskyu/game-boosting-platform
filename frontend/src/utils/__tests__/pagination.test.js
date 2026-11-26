import { describe, expect, it } from 'vitest'

import { buildPageItems } from '../pagination'

function pageNumbers(items) {
  return items.filter((item) => typeof item === 'number')
}

describe('buildPageItems', () => {
  it('returns [1] when there is only one page or no valid page count', () => {
    expect(buildPageItems(1, 1)).toEqual([1])
    expect(buildPageItems(1, 0)).toEqual([1])
    expect(buildPageItems(5, -3)).toEqual([1])
  })

  it('shows every page when total pages are 7 or fewer', () => {
    for (let total = 2; total <= 7; total += 1) {
      expect(buildPageItems(1, total)).toEqual(
        Array.from({ length: total }, (_, index) => index + 1),
      )
      expect(buildPageItems(total, total)).toEqual(
        Array.from({ length: total }, (_, index) => index + 1),
      )
    }
  })

  it('keeps first, last, current and both neighbors for a middle page', () => {
    expect(buildPageItems(50, 100)).toEqual([
      1, 'ellipsis', 49, 50, 51, 'ellipsis', 100,
    ])
    expect(buildPageItems(10, 20)).toEqual([
      1, 'ellipsis', 9, 10, 11, 'ellipsis', 20,
    ])
  })

  it('shifts the window to 1..5 near the first page', () => {
    expect(buildPageItems(1, 10)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 10])
    expect(buildPageItems(2, 10)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 10])
    expect(buildPageItems(3, 10)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 10])
    expect(buildPageItems(4, 10)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 10])
  })

  it('shifts the window to last-4..last near the final page', () => {
    expect(buildPageItems(10, 10)).toEqual([1, 'ellipsis', 6, 7, 8, 9, 10])
    expect(buildPageItems(9, 10)).toEqual([1, 'ellipsis', 6, 7, 8, 9, 10])
    expect(buildPageItems(8, 10)).toEqual([1, 'ellipsis', 6, 7, 8, 9, 10])
  })

  it('normalizes and clamps integer inputs', () => {
    expect(buildPageItems(0, 10)).toEqual(buildPageItems(1, 10))
    expect(buildPageItems(999, 10)).toEqual(buildPageItems(10, 10))
    expect(buildPageItems('3', '10')).toEqual(buildPageItems(3, 10))
    expect(buildPageItems(3.9, 9.9)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 9])
  })

  it('falls back to page 1 for invalid input', () => {
    expect(buildPageItems()).toEqual([1])
    expect(buildPageItems(null, undefined)).toEqual([1])
    expect(buildPageItems('abc', 'xyz')).toEqual([1])
    expect(buildPageItems(Number.NaN, Number.NaN)).toEqual([1])
    expect(buildPageItems(Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY)).toEqual([1])
  })

  it('never exceeds 7 items, never duplicates, and keeps the bounds and neighbors', () => {
    for (let total = 1; total <= 60; total += 1) {
      for (let current = 1; current <= total; current += 1) {
        const items = buildPageItems(current, total)
        const pages = pageNumbers(items)

        expect(items.length).toBeLessThanOrEqual(7)
        expect(new Set(pages).size).toBe(pages.length)
        expect(pages[0]).toBe(1)
        expect(pages[pages.length - 1]).toBe(total)
        expect(pages).toContain(current)
        if (current > 1) expect(pages).toContain(current - 1)
        if (current < total) expect(pages).toContain(current + 1)

        // 省略号只允许出现在真实缺口 > 1 的位置。
        const ellipsisIndexes = items
          .map((item, index) => (item === 'ellipsis' ? index : -1))
          .filter((index) => index >= 0)
        expect(ellipsisIndexes.length).toBeLessThanOrEqual(2)
        for (const index of ellipsisIndexes) {
          const left = items[index - 1]
          const right = items[index + 1]
          expect(typeof left).toBe('number')
          expect(typeof right).toBe('number')
          expect(right - left).toBeGreaterThan(1)
        }
      }
    }
  })
})
