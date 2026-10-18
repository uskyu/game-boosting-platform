import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const modalSource = readFileSync(new URL('../AfterSaleStatusModal.vue', import.meta.url), 'utf8')
const lightboxSource = readFileSync(new URL('../Lightbox.vue', import.meta.url), 'utf8')
const mainCssSource = readFileSync(new URL('../../assets/main.css', import.meta.url), 'utf8')
const adminSource = readFileSync(new URL('../../views/AdminView.vue', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

function slice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing start marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('售后凭证站内预览（微信内不得跳出）', () => {
  it('compiles the status modal and the lightbox', () => {
    compileView(modalSource, 'AfterSaleStatusModal.vue')
    compileView(lightboxSource, 'Lightbox.vue')
  })

  it('renders vouchers as in-page buttons instead of target=_blank links', () => {
    // 绝不能把图片 URL 当新页面打开：微信内置浏览器没有标签页，必然"跳出"
    expect(modalSource).not.toMatch(/target="_blank"/)
    expect(modalSource).not.toMatch(/:href="image\.url"/)
    expect(modalSource).not.toMatch(/window\.open\(/)

    const block = slice(modalSource, '售后凭证', '<div v-if="error"')
    expect(block).toMatch(/<button/)
    expect(block).toMatch(/@click\.stop="openLightbox\(index\)"/)
    expect(block).toMatch(/class="admin-order-thumb"/)
    expect(block).toMatch(/aria-label="`查看售后凭证 \$\{index \+ 1\}`"/)
    expect(block).toMatch(/:src="image\.url"/)
  })

  it('wires the Lightbox with computed images, start-index and close write-back', () => {
    expect(modalSource).toMatch(/import Lightbox from '@\/components\/Lightbox\.vue'/)
    expect(modalSource).toMatch(/const displayImages = computed\(\(\) => imageList\(display\.value\)\)/)
    expect(modalSource).toMatch(/const lightbox = ref\(\{ visible: false, index: 0 \}\)/)
    expect(modalSource).toMatch(/<Lightbox[\s\S]*?:images="displayImages"/)
    expect(modalSource).toMatch(/:visible="lightbox\.visible"/)
    expect(modalSource).toMatch(/:start-index="lightbox\.index"/)
    expect(modalSource).toMatch(/@close="lightbox\.visible = false"/)
    // 弹窗关闭 / 重开时必须复位灯箱：Lightbox 的滚动锁与 ESC 监听挂在非 immediate 的
    // watch(visible) 上，残留 visible=true 会导致"遮罩亮着但不锁滚动、ESC 失效"。
    const watchBlock = slice(modalSource, 'watch(\n  () => props.modelValue', '\n)')
    expect(watchBlock).toMatch(/lightbox\.value = \{ visible: false, index: 0 \}/)
  })

  it('keeps the lightbox inside the scrim so unmounting the modal releases the scroll lock', () => {
    // Lightbox 必须在 modal-scrim 的 v-if 树内：弹窗关闭时随之卸载触发 cleanup，
    // 否则 body.overflow 可能被永久锁死。
    const scrimIndex = modalSource.indexOf('class="modal-scrim"')
    const lightboxIndex = modalSource.indexOf('<Lightbox')
    const teleportEnd = modalSource.indexOf('</teleport>')
    expect(scrimIndex).toBeGreaterThan(-1)
    expect(lightboxIndex).toBeGreaterThan(scrimIndex)
    expect(lightboxIndex).toBeLessThan(teleportEnd)
  })

  it('opens admin vouchers in the existing orderLightbox instead of window.open', () => {
    expect(adminSource).toMatch(/function openAfterSaleLightbox\(item, index\)\s*\{/)
    expect(adminSource).toMatch(/orderLightbox\.value = \{ visible: true, images, index \}/)
    expect(adminSource).not.toMatch(/openAfterSaleImage/)
    expect(adminSource).not.toMatch(/window\.open\(/)
    expect(adminSource).not.toMatch(/target="_blank"/)
  })

  it('defines the thumb styles globally so the teleported modal actually gets them', () => {
    // .admin-order-thumb 原先只在 AdminView 的 <style scoped> 里 → 对 teleport 到
    // body 的售后弹窗无效，缩略图无尺寸约束。现在必须由全局 main.css 提供。
    expect(mainCssSource).toMatch(/\.admin-order-thumb \{[^}]*width: 60px;[^}]*height: 60px;/)
    expect(mainCssSource).toMatch(/\.admin-order-thumb img \{[^}]*object-fit: cover;/)
    expect(mainCssSource).toMatch(/\.admin-order-thumbs \{[^}]*overflow-x: auto;/)
    // AdminView 的 scoped 块里不能再重复定义（单一来源）
    const scopedStyle = adminSource.slice(adminSource.indexOf('<style scoped>'))
    expect(scopedStyle).not.toMatch(/^\.admin-order-thumb/m)
  })
})

describe('Lightbox 微信兼容加固', () => {
  it('keeps physical inset fallbacks for old X5 kernels', () => {
    const overlay = slice(lightboxSource, '.lb-overlay {', '}')
    expect(overlay).toMatch(/top: 0;/)
    expect(overlay).toMatch(/right: 0;/)
    expect(overlay).toMatch(/bottom: 0;/)
    expect(overlay).toMatch(/left: 0;/)
    expect(overlay).toMatch(/inset: 0;/)
    // 兜底必须在 inset 简写之前，否则被简写覆盖没有意义
    expect(overlay.indexOf('top: 0;')).toBeLessThan(overlay.indexOf('inset: 0;'))
  })

  it('keeps a plain top/bottom fallback before the env() safe-area values', () => {
    const close = slice(lightboxSource, '.lb-close {', '}')
    expect(close).toMatch(/top: 16px;/)
    expect(close).toMatch(/top: max\(16px, env\(safe-area-inset-top\)\);/)
    expect(close.indexOf('top: 16px;')).toBeLessThan(
      close.indexOf('top: max(16px, env(safe-area-inset-top));'),
    )

    const caption = slice(lightboxSource, '.lb-caption {', '}')
    expect(caption).toMatch(/bottom: 16px;/)
    expect(caption).toMatch(/bottom: max\(16px, env\(safe-area-inset-bottom\)\);/)
    expect(caption.indexOf('bottom: 16px;')).toBeLessThan(
      caption.indexOf('bottom: max(16px, env(safe-area-inset-bottom));'),
    )
  })
})
