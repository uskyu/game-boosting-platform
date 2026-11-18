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

  it('tightens only hall cards and lets desktop metadata wrap without squeezing title or details action', () => {
    expect(hallSource).toContain('hall-order-card !rounded-[12px] !p-4')
    expect(hallSource).toContain('md:flex-col md:items-stretch md:gap-2')
    expect(hallSource).toContain('md:flex-wrap md:gap-x-2 md:gap-y-1')
    expect(hallSource).toContain('class="tag max-w-[45%] shrink-0 truncate !bg-info-soft !text-info md:hidden"')
    expect(hallSource).toContain('class="tag hidden min-w-0 max-w-full truncate !bg-info-soft !text-info md:inline-flex"')
    expect(hallSource).toContain('md:self-end')
  })
})
