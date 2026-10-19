import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const adminSource = readFileSync(new URL('../../views/AdminView.vue', import.meta.url), 'utf8')
const recordsSource = readFileSync(
  new URL('../../components/admin/AdminRechargeRecords.vue', import.meta.url),
  'utf8'
)
const storeSource = readFileSync(new URL('../wallet.js', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('admin payment records (支付记录)', () => {
  it('compiles the records page and AdminView', () => {
    compileView(recordsSource, 'AdminRechargeRecords.vue')
    compileView(adminSource, 'AdminView.vue')
  })

  it('registers a 支付记录 tab wired to the records component', () => {
    expect(adminSource).toMatch(/'recharges'/)
    expect(adminSource).toMatch(/支付记录/)
    expect(adminSource).toMatch(/AdminRechargeRecords/)
    expect(adminSource).toMatch(/activeTab === 'recharges'/)
  })

  it('is admin-only, same as 支付设置', () => {
    // tab 按钮必须带 v-if="isAdmin"，否则普通用户也能看到入口
    const tabBlock = adminSource.slice(
      adminSource.indexOf("activeTab === 'recharges'") - 120,
      adminSource.indexOf("activeTab === 'recharges'") + 40
    )
    expect(tabBlock).toMatch(/v-if="isAdmin"/)
  })

  it('lists every user recharge and shows callback state', () => {
    // 列表要能看到「回调成功 / 未回调」——老板要的就是这个
    expect(recordsSource).toMatch(/callbackLabel/)
    expect(recordsSource).toMatch(/回调成功/)
    expect(recordsSource).toMatch(/epay_trade_no/)
    // 用户信息随行展示
    expect(recordsSource).toMatch(/userName\(item\)/)
  })

  it('offers both 改为成功 and 改为失败 actions', () => {
    expect(recordsSource).toMatch(/openDecision\(item, 'SUCCESS'\)/)
    expect(recordsSource).toMatch(/openDecision\(item, 'FAILED'\)/)
    expect(recordsSource).toMatch(/改为成功/)
    expect(recordsSource).toMatch(/改为失败/)
  })

  it('only allows the transitions the backend state machine accepts', () => {
    // PENDING/FAILED 才能置成功；PENDING/SUCCESS 才能置失败。
    // 与后端 admin_update_status 的状态机一一对应，前端置灰只是体验层。
    const successFn = recordsSource.slice(
      recordsSource.indexOf('function canMarkSuccess'),
      recordsSource.indexOf('function canMarkFailed')
    )
    expect(successFn).toMatch(/PENDING/)
    expect(successFn).toMatch(/FAILED/)
    expect(successFn).not.toMatch(/SUCCESS/)

    const failedFn = recordsSource.slice(
      recordsSource.indexOf('function canMarkFailed'),
      recordsSource.indexOf('function openDecision')
    )
    expect(failedFn).toMatch(/PENDING/)
    expect(failedFn).toMatch(/SUCCESS/)
    expect(failedFn).not.toMatch(/'FAILED'/)
  })

  it('requires a reason when marking as failed', () => {
    expect(recordsSource).toMatch(/state\.target === 'FAILED' && !reason/)
    expect(recordsSource).toMatch(/请填写改为失败的原因/)
  })

  it('tells the admin exactly whose money moves, before they confirm', () => {
    // 「将给 X 增加 ¥Y」/「将从 X 扣回 ¥Y」/「不动余额」三种口径都必须存在
    expect(recordsSource).toMatch(/可用余额增加/)
    expect(recordsSource).toMatch(/可用余额扣回/)
    expect(recordsSource).toMatch(/不动余额/)
  })

  it('wires through the wallet store to the admin endpoints', () => {
    expect(storeSource).toMatch(/\/admin\/payment\/recharges/)
    expect(storeSource).toMatch(/fetchAdminRecharges/)
    expect(storeSource).toMatch(/setAdminRechargeStatus/)
  })
})

describe('recharge status display metadata', () => {
  it('covers every backend RechargeStatus value, including FAILED', () => {
    expect(storeSource).toMatch(/PENDING:/)
    expect(storeSource).toMatch(/SUCCESS:/)
    expect(storeSource).toMatch(/CLOSED:/)
    expect(storeSource).toMatch(/FAILED:/)
  })

  it('labels the new reversal transaction type', () => {
    // 冲正流水不登记会直接显示英文 RECHARGE_REVERSAL
    expect(storeSource).toMatch(/RECHARGE_REVERSAL:\s*\{\s*label:/)
  })

  it('user wallet page reuses the shared status meta instead of a private copy', () => {
    const walletView = readFileSync(
      new URL('../../views/WalletView.vue', import.meta.url),
      'utf8'
    )
    expect(walletView).toMatch(/getRechargeStatusMeta/)
    // 旧的私有表已移除，避免两处文案不同步
    expect(walletView).not.toMatch(/const RECHARGE_STATUS_META = \{/)
  })
})
