import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const adminSource = readFileSync(new URL('../AdminView.vue', import.meta.url), 'utf8')
const modalSource = readFileSync(new URL('../../components/AfterSaleStatusModal.vue', import.meta.url), 'utf8')
const storeSource = readFileSync(new URL('../../stores/afterSales.js', import.meta.url), 'utf8')

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

describe('admin after-sale review actions', () => {
  it('compiles AdminView and the status modal', () => {
    compileView(adminSource, 'AdminView.vue')
    compileView(modalSource, 'AfterSaleStatusModal.vue')
  })

  it('exposes three actions on PENDING after-sales', () => {
    const pendingBlock = slice(adminSource, "v-if=\"item.status === 'PENDING'\"", '已处理')
    expect(pendingBlock).toMatch(/openAfterSaleApproveModal\(item\)/)
    expect(pendingBlock).toMatch(/openAfterSaleRejectModal\(item\)/)
    expect(pendingBlock).toMatch(/openAfterSaleCloseModal\(item\)/)
    expect(pendingBlock).toMatch(/售后成功/)
    expect(pendingBlock).toMatch(/驳回/)
    expect(pendingBlock).toMatch(/取消售后/)
  })

  it('requires a reason for both reject and close', () => {
    expect(adminSource).toMatch(/state\.type === 'after-sale-close' && !state\.reason\.trim\(\)/)
    expect(adminSource).toMatch(/state\.error = '请填写取消原因'/)
    expect(adminSource).toMatch(/state\.error = '请填写驳回原因'/)
  })

  it('lets the admin adjust the deduction amount within the applied amount', () => {
    // 裁定额输入框：默认=申请金额，max=申请金额
    expect(adminSource).toMatch(/id="after-sale-approve-amount"/)
    expect(adminSource).toMatch(/:max="modal\.appliedAmount"/)
    expect(adminSource).toMatch(/appliedAmount: item\.amount/)
    // 留空=按申请金额全额（整个字段不发）；填了必须 >0 且不超过申请金额
    const guard = slice(adminSource, "state.type === 'after-sale-approve' && state.amount !== ''", 'mark-paid')
    expect(guard).toMatch(/amount <= 0 \|\| amount > Number\(state\.appliedAmount\)/)
    expect(adminSource).toMatch(/action === 'approve' && state\.amount !== '' && state\.amount != null/)
    // close 不传 amount（不涉及扣款）
    expect(adminSource).not.toMatch(/action === 'approve' \? \{ amount: Number\(state\.amount\) \} : \{\}/)
  })

  it('maps the three actions to the store payload', () => {
    expect(adminSource).toMatch(/const action = state\.type === 'after-sale-close' \? 'close'/)
    expect(storeSource).toMatch(/if \(payload\.amount != null\) body\.amount = payload\.amount/)
    expect(storeSource).toMatch(/async function withdrawAfterSale\(orderId, afterSaleId\)/)
    expect(storeSource).toMatch(/CLOSED: \{ label: '已取消'/)
  })

  it('labels CLOSED in the status meta and filter options', () => {
    expect(storeSource).toMatch(/\{ value: 'CLOSED', label: '已取消' \}/)
    expect(adminSource).toMatch(/取消仅关闭案件，不涉及扣款/)
  })
})

describe('publisher withdraw modal', () => {
  it('loads the order after-sales and withdraws with two-step confirm', () => {
    expect(modalSource).toMatch(/store\.fetchOrderAfterSales\(props\.orderId\)/)
    expect(modalSource).toMatch(/store\.withdrawAfterSale\(props\.orderId, target\.id\)/)
    // 两步确认：第一次切入确认态，第二次才真撤
    expect(modalSource).toMatch(/if \(!confirmWithdraw\.value\)\s*\{\s*confirmWithdraw\.value = true\s*return/)
    expect(modalSource).toMatch(/emit\('withdrawn'\)/)
  })

  it('only offers withdraw while a PENDING after-sale exists', () => {
    expect(modalSource).toMatch(/const pending = computed\(\(\) => items\.value\.find\(\(item\) => item\.status === 'PENDING'\) \|\| null\)/)
    expect(modalSource).toMatch(/v-if="pending"/)
  })
})
