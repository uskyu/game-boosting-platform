import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')

describe('order hall responsive cards', () => {
  it('keeps mobile single-column, tablet two-column, and desktop three-column grids', () => {
    const { descriptor, errors } = parse(hallSource, { filename: 'OrderHallView.vue' })
    expect(errors).toEqual([])
    expect(() => compileScript(descriptor, { id: 'OrderHallView.vue' })).not.toThrow()
    expect(hallSource).toContain('grid grid-cols-1 gap-4 md:gap-3 md:grid-cols-2 xl:grid-cols-3')
  })

  it('tightens only hall cards and keeps the footer on one consistent, wrapping layout', () => {
    expect(hallSource).toContain('hall-order-card !rounded-[12px] !p-4')
    // 底栏不再在桌面切列：两种断点同一套换行布局
    expect(hallSource).not.toContain('md:flex-col md:items-stretch md:gap-2')
    expect(hallSource).toContain('flex flex-wrap items-center gap-2 text-[13px]')
    expect(hallSource).toContain('class="tag min-w-0 max-w-full truncate !bg-info-soft !text-info"')
    // 发单人昵称只在底栏出现一次
    expect(hallSource.match(/发单人 ·/g)).toHaveLength(1)
  })
})
