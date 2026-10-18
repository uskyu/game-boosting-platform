import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const modalSource = readFileSync(new URL('../../components/AfterSaleStatusModal.vue', import.meta.url), 'utf8')
const detailSource = readFileSync(new URL('../OrderDetail.vue', import.meta.url), 'utf8')
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

describe('售后弹窗双角色（viewerRole）', () => {
  it('compiles the modal with the viewerRole prop and accepted emit', () => {
    compileView(modalSource, 'AfterSaleStatusModal.vue')
    expect(modalSource).toMatch(/viewerRole: \{ type: String, default: 'boss' \}/)
    expect(modalSource).toMatch(/defineEmits\(\['update:modelValue', 'withdrawn', 'accepted'\]\)/)
    expect(modalSource).toMatch(/const isBoosterView = computed\(\(\) => props\.viewerRole === 'booster'\)/)
  })

  it('renders 撤回售后 for the boss view (default) and 同意退款 for the booster view', () => {
    // 打手主按钮：仅 booster + 有 PENDING 时出现，两步文案带申请金额
    const acceptButton = slice(modalSource, 'v-if="pending && isBoosterView"', '@click="handleAccept"')
    expect(acceptButton).toMatch(/class="btn-danger flex-1"/)
    expect(acceptButton).toMatch(/:disabled="accepting \|\| assistLoading"/)
    expect(modalSource).toMatch(/再点一次确认扣款 \$\{formatPrice\(pending\.amount\)\}/)
    expect(modalSource).toMatch(/同意退款 \$\{formatPrice\(pending\.amount\)\}/)
    // 老板主按钮仍是撤回，且与同意退款互斥（v-else-if）
    const withdrawButton = slice(modalSource, 'v-else-if="pending"', '{{ withdrawing ?')
    expect(withdrawButton).toMatch(/@click="handleWithdraw"/)
    expect(modalSource).toMatch(/{{ withdrawing \? '撤回中\.\.\.' : confirmWithdraw \? '再点一次确认撤回' : '撤回售后' \}\}/)
    // 角色说明文案分流：打手侧讲扣划，老板侧讲提现限制
    expect(modalSource).toMatch(/<template v-if="isBoosterView">\s*老板发起了售后/)
    expect(modalSource).toMatch(/<template v-else>\s*订单 #\{\{ display\.order_id \}\} 的售后申请/)
    // 扣划说明：同意后退款即时从余额扣给老板并结案
    expect(modalSource).toMatch(/同意退款将立即从你的余额扣划 \{\{ formatPrice\(pending\.amount\) \}\} 给老板并结案/)
  })

  it('confirms acceptance in two steps before calling acceptAfterSale', async () => {
    // 第一次点只切入确认态，第二次才真扣（与撤回售后同款模式）
    const accept = slice(modalSource, 'async function handleAccept(', '\n}')
    expect(accept).toMatch(/if \(!confirmAccept\.value\)\s*\{\s*confirmAccept\.value = true\s*return/)
    expect(accept).toMatch(/await store\.acceptAfterSale\(props\.orderId, target\.id\)/)
    // 成功后通知父页并关闭，失败只留错误文案、不关弹窗
    expect(accept).toMatch(/emit\('accepted'\)/)
    expect(accept).toMatch(/emit\('update:modelValue', false\)/)
    expect(accept).toMatch(/error\.value = result\.error \|\| '同意退款失败，请稍后重试'/)
    // store 侧端点契约
    expect(storeSource).toMatch(/async function acceptAfterSale\(orderId, afterSaleId\)/)
    expect(storeSource).toMatch(/api\.post\(`\/orders\/\$\{orderId\}\/after-sales\/\$\{afterSaleId\}\/accept`\)/)
    // 打开时复位两步态，避免上次的确认态残留
    expect(modalSource).toMatch(/confirmWithdraw\.value = false\s*confirmAccept\.value = false\s*load\(\)/)
  })

  it('keeps 联系平台协助 in the booster view on the same admin-conversation chain', () => {
    const assistButton = slice(modalSource, 'v-if="isBoosterView && pending"', '{{ assistLoading ?')
    expect(assistButton).toMatch(/class="btn-secondary flex-1"/)
    expect(assistButton).toMatch(/:disabled="assistLoading \|\| accepting"/)
    const assist = slice(modalSource, 'async function handleAssist(', '\n}')
    // 直达管理员会话并预填案情（与 OrderDetail/App 兜底入口同链路）
    expect(assist).toMatch(/await chatStore\.startAdminConversation\(\{[\s\S]*?presetMessage: buildAfterSaleAssistMessage\(\{/)
    expect(assist).toMatch(/router\.push\(\{ name: 'chat-detail', params: \{ id: result\.data\.id \} \}\)/)
    // stale / 失败都有兜底文案，finally 必复位 loading
    expect(assist).toMatch(/会话已创建，请到消息中心查看/)
    expect(assist).toMatch(/assistLoading\.value = false/)
    // 无 PENDING 时打手视图也只留「关闭」，不暴露任何操作
    expect(modalSource).toMatch(/<button v-else type="button" class="btn-secondary flex-1" @click="close">关闭<\/button>/)
  })
})

describe('订单详情打手侧售后入口', () => {
  it('opens the after-sale modal instead of starting the admin chat directly', () => {
    const chip = slice(detailSource, 'v-if="showAfterSaleAssist"', '售后处理中')
    expect(chip).toMatch(/@click="showAfterSaleModal = true"/)
    // 不再直达聊天：chip 上不起会话、不带预填案情
    expect(chip).not.toMatch(/handleContactAdmin/)
    expect(chip).not.toMatch(/presetMessage/)
    expect(detailSource).not.toMatch(/afterSaleAssistMessage = computed/)
    expect(detailSource).not.toMatch(/import \{ buildAfterSaleAssistMessage \}/)
    // 文案去掉「联系平台协助」：点击开面板而非直接聊天，避免误导
    expect(detailSource).toMatch(/售后处理中·查看详情/)
  })

  it('wires the modal in booster mode and silently refreshes on acceptance', () => {
    const modal = slice(detailSource, '<AfterSaleStatusModal', '/>')
    expect(modal).toMatch(/v-model="showAfterSaleModal"/)
    expect(modal).toMatch(/:order-id="order\.id"/)
    expect(modal).toMatch(/:viewer-role="'booster'"/)
    expect(modal).toMatch(/@accepted="onAfterSaleAccepted"/)
    // 静默刷新：非静默会把全局 loading 置真，与广播触发的静默刷新竞争后白屏
    const accepted = slice(detailSource, 'function onAfterSaleAccepted(', '\n}')
    expect(accepted).toMatch(/ordersStore\.fetchOrder\(orderId, \{ silent: true \}\)/)
    expect(accepted).toMatch(/successMessage\.value = '已同意退款，款项已划给老板，售后结案'/)
  })
})

describe('订单详情老板侧售后入口（2026-10-18 从我的派单列表迁入）', () => {
  it('mounts a second boss-side modal that defaults to viewerRole="boss"', () => {
    // 打手侧实例保留 viewer-role="booster"；老板侧实例不传，走组件默认 'boss'
    const bossModal = slice(detailSource, 'v-model="showAfterSaleStatusModal"', '/>')
    expect(bossModal).toMatch(/:order-id="order\.id"/)
    expect(bossModal).toMatch(/@withdrawn="onAfterSaleWithdrawn"/)
    // 老板实例不能带 booster 视角，否则显示「同意退款」而不是「撤回售后」
    expect(bossModal).not.toMatch(/viewer-role/)
    expect(modalSource).toMatch(/viewerRole: \{ type: String, default: 'boss' \}/)
  })

  it('guards the one-approval-per-order rule before entering the apply page', () => {
    // after_sale_pending 只覆盖 PENDING；APPROVED 后按钮会「复活」，
    // 这里按需补拉该单售后记录并禁用，避免老板填完表单才被后端 400 拒。
    const gate = slice(detailSource, 'async function refreshAfterSaleGate(', '\n}')
    expect(gate).toMatch(/afterSalesStore\.fetchOrderAfterSales\(orderId\)/)
    expect(gate).toMatch(/item\.status === 'APPROVED'/)
    expect(gate).toMatch(/isOwner\.value/)
    expect(gate).toMatch(/order\.value\?\.status !== 'COMPLETED'/)
    // 详情加载完成后异步补算，不阻塞首屏骨架屏
    expect(detailSource).toMatch(/refreshAfterSaleGate\(orderId\)\.catch\(\(\) => \{\}\)/)
    // 禁用态沿用 operator 顺序：pending 可点（撤回），已裁定才禁用
    const buttonIndex = detailSource.indexOf('goToAfterSaleApply(order.id)')
    const button = detailSource.slice(buttonIndex - 600, buttonIndex + 400)
    expect(button).toMatch(/:disabled="afterSaleApproved"/)
  })
})
