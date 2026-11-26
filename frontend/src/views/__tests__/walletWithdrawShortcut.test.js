import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const walletSource = readFileSync(new URL('../WalletView.vue', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('钱包页「提现」快捷入口（老板 2026-11-26 反馈）', () => {
  it('compiles WalletView', () => {
    compileView(walletSource, 'WalletView.vue')
  })

  it('hero 区域在充值旁边提供提现按钮，点击滚动到申请提现区', () => {
    expect(walletSource).toContain('@click="scrollToWithdraw"')
    const buttonIndex = walletSource.indexOf('@click="scrollToWithdraw"')
    const button = walletSource.slice(buttonIndex - 400, buttonIndex + 160)
    expect(button).toContain('btn-secondary')
    expect(button).toContain('提现')
  })

  it('申请提现卡片是带滚动偏移的定位目标，并短暂高亮', () => {
    expect(walletSource).toContain('id="withdraw-section"')
    expect(walletSource).toContain('ref="withdrawSectionRef"')
    expect(walletSource).toContain('scroll-mt-24')
    expect(walletSource).toContain("scrollIntoView({ behavior: 'smooth', block: 'start' })")
    expect(walletSource).toContain("'ring-2 ring-primary/40': withdrawHighlighted")
  })
})
