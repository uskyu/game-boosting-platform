import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')

// 大厅卡片区域：从网格 section 开始，到卡片 article 结束（避开下方「今日已接单」列表）
const cardZone = (() => {
  const start = hallSource.indexOf("<article\n        v-for=\"order in visibleOrders\"")
  expect(start, 'missing hall order card article').toBeGreaterThan(-1)
  const end = hallSource.indexOf('</article>', start)
  expect(end).toBeGreaterThan(start)
  return hallSource.slice(start, end)
})()

function slice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('hall order card layout', () => {
  it('compiles the hall view', () => {
    const { descriptor, errors } = parse(hallSource, { filename: 'OrderHallView.vue' })
    expect(errors).toEqual([])
    expect(() => compileScript(descriptor, { id: 'OrderHallView.vue' })).not.toThrow()
  })

  it('drops the desktop column-switch trick from the card footer', () => {
    expect(hallSource).not.toContain('md:flex-col md:items-stretch')
    expect(cardZone).not.toContain('md:flex-col')
  })

  it('lets the title run to two lines instead of truncating to one', () => {
    const title = slice(cardZone, '<p v-if="order.title"', '</p>')
    expect(title).toContain('line-clamp-2')
    expect(title).toContain('break-words')
    expect(title).not.toContain('truncate')
  })

  it('clamps the summary to two lines at 13px and caps the generated text', () => {
    const summary = slice(cardZone, '<p v-if="buildSummary(order)"', '</p>')
    expect(summary).toContain('line-clamp-2')
    expect(summary).toContain('text-[13px]')
    expect(summary).toContain('text-ink-2')
    expect(summary).not.toContain('truncate')

    // 摘要生成：88 字上限 + 省略号，多行/多空白压成一行
    expect(hallSource).toMatch(/const SUMMARY_MAX_CHARS = 88/)
    expect(hallSource).toMatch(/replace\(\/\\s\+\/g, ' '\)/)
    expect(hallSource).toMatch(/SUMMARY_MAX_CHARS\)\.trimEnd\(\)\}…/)
  })

  it('renders compensation / payout / claim count as wrap-friendly chips', () => {
    expect(hallSource).toMatch(/function getMetaChips\(order\)/)
    expect(hallSource).toContain('key: \'compensation\'')
    expect(hallSource).toContain('key: \'payout\'')
    expect(hallSource).toContain('key: \'claims\'')
    expect(cardZone).toMatch(/v-for="chip in getMetaChips\(order\)"/)
    expect(cardZone).toContain('flex flex-wrap items-center gap-1.5')
    expect(cardZone).not.toContain('getMetaLine(order)')
  })

  it('splits the footer into a chips row and an id/time/action row', () => {
    const footer = slice(cardZone, 'mt-4 border-t border-line-1 pt-3', '<button')

    // Row A：游戏名 + 发单人 + 未读，平铺换行
    const chipsRow = slice(footer, 'flex flex-wrap items-center gap-2 text-[13px]', 'mt-3 flex items-center justify-between')
    expect(chipsRow).toContain('{{ order.game_name }}')
    expect(chipsRow).toMatch(/发单人 · \{\{ getPublisherNickname\(order\) \}\}/)
    expect(chipsRow).toContain('消息 {{ getOrderUnreadCount(order.id) }}')
    // Row A 不能再塞时间/编号，否则又回到一行挤在一起的旧问题
    expect(chipsRow).not.toContain('发布于')
    expect(chipsRow).not.toContain('#{{ order.id }}')

    // Row B：左侧 #id · 时间，右侧查看详情
    const actionRow = slice(cardZone, 'mt-3 flex items-center justify-between gap-3', '<button')
    expect(actionRow).toContain('#{{ order.id }}')
    expect(actionRow).toContain('发布于 {{ formatShortDate(order.created_at) }}')
    expect(actionRow).not.toContain('truncate')
    expect(cardZone).toMatch(/查看详情 →\s*<\/button>/)
    expect(cardZone).toMatch(/class="btn-ghost shrink-0 !min-h-\[36px\] !px-4 !py-1\.5"/)
  })

  it('shows the publisher nickname exactly once in the whole card', () => {
    expect(cardZone.match(/发单人 ·/g)).toHaveLength(1)
    expect(hallSource.match(/发单人 ·/g)).toHaveLength(1)
  })
})
