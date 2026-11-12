import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const adminSource = readFileSync(new URL('../../views/AdminView.vue', import.meta.url), 'utf8')
const ledgerSource = readFileSync(
  new URL('../../components/admin/AdminDepositLedger.vue', import.meta.url),
  'utf8'
)
const storeSource = readFileSync(new URL('../adminDepositLedger.js', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('admin deposit ledger (保证金流水)', () => {
  it('compiles the ledger page and AdminView', () => {
    compileView(ledgerSource, 'AdminDepositLedger.vue')
    compileView(adminSource, 'AdminView.vue')
  })

  it('registers an admin-only 保证金流水 tab wired to the ledger component', () => {
    expect(adminSource).toMatch(/'depositLedger'/)
    expect(adminSource).toMatch(/保证金流水/)
    expect(adminSource).toMatch(/AdminDepositLedger/)
    expect(adminSource).toMatch(/activeTab === 'depositLedger'/)
    const tabBlock = adminSource.slice(
      adminSource.indexOf("activeTab === 'depositLedger'") - 120,
      adminSource.indexOf("activeTab === 'depositLedger'") + 60
    )
    expect(tabBlock).toMatch(/v-if="isAdmin"/)
  })

  it('queries the deposit transfer endpoint through the store', () => {
    expect(storeSource).toMatch(/\/admin\/deposit\/transactions/)
    expect(storeSource).toMatch(/fetchLedger/)
    expect(storeSource).toMatch(/direction/)
  })

  it('shows who transferred out and how much', () => {
    expect(ledgerSource).toMatch(/保证金转回/)
    expect(ledgerSource).toMatch(/用户自助转回人数/)
    expect(ledgerSource).toMatch(/self_out_amount/)
    expect(ledgerSource).toMatch(/deposit_change/)
    expect(ledgerSource).toMatch(/operatorLabel/)
    expect(ledgerSource).toMatch(/用户自助/)
    expect(ledgerSource).toMatch(/不动可用余额/)
  })

  it('defaults to the product timezone today, not browser local midnight', () => {
    expect(ledgerSource).toMatch(/Intl.DateTimeFormat/)
    expect(ledgerSource).toMatch(/Asia\/Shanghai/)
    expect(ledgerSource).toMatch(/productDateToday/)
  })

  it('supports date range, direction and user search', () => {
    expect(ledgerSource).toMatch(/开始日期/)
    expect(ledgerSource).toMatch(/结束日期/)
    expect(ledgerSource).toMatch(/用户名 \/ 邮箱 \/ 用户ID/)
    expect(ledgerSource).toMatch(/DIRECTION_OPTIONS/)
    expect(ledgerSource).toMatch(/setDirection/)
  })
})
