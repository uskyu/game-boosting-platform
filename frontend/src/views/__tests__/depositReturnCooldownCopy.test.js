import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const depositViewSource = readFileSync(
  new URL('../DepositView.vue', import.meta.url),
  'utf8'
)
const walletViewSource = readFileSync(
  new URL('../WalletView.vue', import.meta.url),
  'utf8'
)
const storeSource = readFileSync(new URL('../../stores/wallet.js', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('保证金转回余额：文案写明冷却天数（老板 2026-10-19）', () => {
  // 老板原话：「把保证金转回可用余额。若存在未完成订单，最后一单完结后
  // 冷却七天即可转回余额」——目的是让打手提前知道要等多久，不必点下去被拒。
  it('compiles both views', () => {
    compileView(depositViewSource, 'DepositView.vue')
    compileView(walletViewSource, 'WalletView.vue')
  })

  it('spells out the cooldown in the 转回余额 section text', () => {
    const section = depositViewSource.slice(
      depositViewSource.indexOf('转回余额'),
      depositViewSource.indexOf('</section>', depositViewSource.indexOf('转回余额'))
    )
    expect(section).toMatch(/冷却/)
    expect(section).toMatch(/cooldownDays/)
    // 不再只写含糊的「需满足冷却时间」
    expect(section).not.toMatch(/需满足冷却时间后才可转回/)
  })

  it('reads the cooldown from the backend instead of hardcoding 7', () => {
    // 后台「保证金管理」可改冷却天数，前端必须跟着走，不能写死文案
    expect(depositViewSource).toMatch(/return_cooldown_days/)
    expect(depositViewSource).toMatch(/const cooldownDays = computed/)
    expect(storeSource).toMatch(/return_cooldown_days: toNumber\(source\.return_cooldown_days, 7\)/)
  })

  it('shows the cooldown in the wallet card as well', () => {
    expect(walletViewSource).toMatch(/冷却/)
    expect(walletViewSource).toMatch(/return_cooldown_days/)
  })
})
