import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const listSource = readFileSync(new URL('../OrderList.vue', import.meta.url), 'utf8')
const detailSource = readFileSync(new URL('../OrderDetail.vue', import.meta.url), 'utf8')
const walletSource = readFileSync(new URL('../WalletView.vue', import.meta.url), 'utf8')
const adminSource = readFileSync(new URL('../AdminView.vue', import.meta.url), 'utf8')
const applySource = readFileSync(new URL('../AfterSaleApplyView.vue', import.meta.url), 'utf8')
const routerSource = readFileSync(new URL('../../router/index.js', import.meta.url), 'utf8')

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

describe('售后入口按钮（OrderDetail 右侧「操作」卡，2026-10-18 从列表迁入）', () => {
  it('compiles OrderDetail with the after-sale button', () => {
    compileView(detailSource, 'OrderDetail.vue')
  })

  it('renders the danger-styled 售后 button only for owner on COMPLETED orders', () => {
    const buttonIndex = detailSource.indexOf('goToAfterSaleApply(order.id)')
    expect(buttonIndex).toBeGreaterThan(-1)
    const button = detailSource.slice(buttonIndex - 600, buttonIndex + 400)
    expect(button).toMatch(/v-if="isOwner && order\.status === 'COMPLETED'"/)
    // 与「申请取消」同款通栏红胶囊：老板要求「红框跟申请取消一样」
    expect(button).toMatch(/class="od-ops__chip btn-danger w-full py-3"/)
    // pending 时改开状态弹窗（可查看案情并撤回）
    expect(button).toMatch(/order\.after_sale_pending \? \(showAfterSaleStatusModal = true\) : goToAfterSaleApply\(order\.id\)/)
    expect(button).toMatch(/\{\{ afterSaleApproved \? '售后已处理' : \(order\.after_sale_pending \? '售后处理中' : '售后'\) \}\}/)
  })

  it('sits directly above 重建订单 in the ops card (老板：放到「重建订单」上面)', () => {
    expect(detailSource.indexOf('goToAfterSaleApply(order.id)'))
      .toBeLessThan(detailSource.indexOf('rebuildOrder(order.id)'))
  })

  it('stays clickable while pending so the owner can withdraw', () => {
    const buttonIndex = detailSource.indexOf('goToAfterSaleApply(order.id)')
    const button = detailSource.slice(buttonIndex - 600, buttonIndex + 400)
    // 处理中只降不透明度，不能 :disabled —— 禁用后老板没法进弹窗撤诉
    expect(button).toMatch(/:class="order\.after_sale_pending \? 'opacity-60' : ''"/)
    expect(button).not.toMatch(/:disabled="order\.after_sale_pending"/)
  })

  it('wires the boss-side AfterSaleStatusModal with withdraw refresh', () => {
    expect(detailSource).toMatch(/import AfterSaleStatusModal from '@\/components\/AfterSaleStatusModal\.vue'/)
    expect(detailSource).toMatch(/v-model="showAfterSaleStatusModal"/)
    expect(detailSource).toMatch(/@withdrawn="onAfterSaleWithdrawn"/)
    // 撤回成功后静默刷新详情并重算「一单一裁」判据
    const withdrawn = slice(detailSource, 'function onAfterSaleWithdrawn(', '\n}')
    expect(withdrawn).toMatch(/ordersStore\.fetchOrder\(orderId, \{ silent: true \}\)/)
    expect(withdrawn).toMatch(/refreshAfterSaleGate\(orderId\)/)
  })

  it('navigates to the after-sale apply route by name', () => {
    const nav = slice(detailSource, 'function goToAfterSaleApply(', '\n}')
    expect(nav).toMatch(/router\.push\(\{ name: 'afterSaleApply', params: \{ id: orderId \} \}\)/)
  })
})

describe('列表侧只读售后标记（OrderList 我的派单卡片）', () => {
  it('compiles OrderList without the migrated button', () => {
    compileView(listSource, 'OrderList.vue')
  })

  it('no longer carries the after-sale action button or its modal', () => {
    // 申请入口整体迁到详情页：列表里不能再有售后按钮/弹窗，避免两处维护
    expect(listSource).not.toMatch(/goToAfterSaleApply/)
    expect(listSource).not.toMatch(/openAfterSaleStatus/)
    expect(listSource).not.toMatch(/<AfterSaleStatusModal/)
    expect(listSource).not.toMatch(/afterSaleStatusOpen/)
    // 也不该残留未使用的 import
    expect(listSource).not.toMatch(/import AfterSaleStatusModal/)
  })

  it('keeps a read-only 售后处理中 badge in the card badge row', () => {
    // 老板要能在列表里看出哪单被发起了售后（与打手侧 claims 卡同款 amber tag）
    const badge = slice(listSource, '<span v-if="order.after_sale_pending"', '</span>')
    expect(badge).toContain('售后处理中')
    expect(badge).toMatch(/tag !bg-warning-soft !text-warning/)
  })
})

describe('售后申请路由', () => {
  it('registers /orders/:id/after-sale/apply as afterSaleApply', () => {
    expect(routerSource).toMatch(/path: '\/orders\/:id\/after-sale\/apply'/)
    expect(routerSource).toMatch(/name: 'afterSaleApply'/)
    expect(routerSource).toMatch(/component: \(\) => import\('@\/views\/AfterSaleApplyView\.vue'\)/)
    const routeStart = routerSource.indexOf("name: 'afterSaleApply'")
    const route = routerSource.slice(routeStart - 200, routeStart + 400)
    expect(route).toMatch(/meta: \{ title: '售后申请', requiresAuth: true \}/)
    // 挂在订单详情路由旁边（动态段兼容）
    expect(routerSource.indexOf("name: 'afterSaleApply'"))
      .toBeGreaterThan(routerSource.indexOf("name: 'order-detail'"))
  })
})

describe('售后申请页（AfterSaleApplyView）', () => {
  it('compiles the apply view', () => {
    compileView(applySource, 'AfterSaleApplyView.vue')
  })

  it('loads the order summary through ordersStore.fetchOrder with loading/error states', () => {
    expect(applySource).toMatch(/await ordersStore\.fetchOrder\(orderId\)/)
    expect(applySource).toMatch(/const loadingOrder = ref\(true\)/)
    expect(applySource).toMatch(/v-else-if="orderError"[\s\S]{0,200}重试/)
  })

  it('keeps the 5-500 reason bounds with a live counter and blocks short submits', () => {
    expect(applySource).toMatch(/const REASON_MIN = 5/)
    expect(applySource).toMatch(/const REASON_MAX = 500/)
    expect(applySource).toMatch(/:maxlength="REASON_MAX"/)
    expect(applySource).toMatch(/\{\{ reasonLen \}\}\/\{\{ REASON_MAX \}\}/)
    expect(applySource).toMatch(/reasonLen\.value >= REASON_MIN/)
    expect(applySource).toMatch(/售后原因至少填写 \$\{REASON_MIN\} 个字/)
  })

  it('caps vouchers at 9 JPG/PNG files of 10MB and uploads each on pick', () => {
    expect(applySource).toMatch(/const MAX_FILES = 9/)
    expect(applySource).toMatch(/const MAX_SIZE = 10 \* 1024 \* 1024/)
    expect(applySource).toMatch(/accept="image\/jpeg,image\/png"/)
    expect(applySource).toMatch(/await compressImageFile\(file\)/)
    expect(applySource).toMatch(/await afterSalesStore\.uploadAfterSaleImage\(orderId, prepared\)/)
    expect(applySource).toMatch(/仅支持 JPG \/ PNG 格式/)
    expect(applySource).toMatch(/单张不能超过 10MB/)
    // 上传期间禁用提交
    expect(applySource).toMatch(/&& !uploading\.value/)
    expect(applySource).toMatch(/:disabled="uploading \|\| images\.length >= MAX_FILES"/)
  })

  it('supports deleting uploaded vouchers', () => {
    const remove = slice(applySource, 'function removeImage(', '\n  }')
    expect(remove).toMatch(/images\.value\.splice\(index, 1\)/)
    expect(applySource).toMatch(/@click="removeImage\(index\)"/)
  })

  it('bounds the amount by the order price with the required error copy', () => {
    expect(applySource).toMatch(/:max="orderPrice \|\| undefined"/)
    expect(applySource).toMatch(/售后金额不能超过订单派单价 \$\{formatPrice\(orderPrice\.value\)\}/)
    expect(applySource).toMatch(/售后金额必须大于 0/)
  })

  it('submits reason/amount/images and goes back on success', () => {
    const submit = slice(applySource, 'async function handleSubmit(', '\n  } catch')
    expect(submit).toMatch(/reason: reason\.value\.trim\(\)/)
    expect(submit).toMatch(/amount: amountValue\.value/)
    expect(submit).toMatch(/images: images\.value\.map\(\(item\) => item\.url\)\.filter\(Boolean\)/)
    expect(submit).toMatch(/await afterSalesStore\.createAfterSale\(orderId, \{/)
    expect(submit).toMatch(/submitError\.value = result\.error \|\| '提交失败，请稍后重试'/)
    expect(applySource).toMatch(/router\.back\(\)/)
  })
})

describe('钱包页售后禁提现（WalletView）', () => {
  it('compiles WalletView with the after-sale guard', () => {
    compileView(walletSource, 'WalletView.vue')
  })

  it('fetches pending booster after-sales on mount', () => {
    expect(walletSource).toMatch(/import \{ useAfterSalesStore \} from '@\/stores\/afterSales'/)
    const guard = slice(walletSource, 'async function fetchAfterSaleGuard()', '\n}')
    expect(guard).toMatch(/if \(authStore\.isAdmin\) return/)
    expect(guard).toMatch(/await afterSalesStore\.fetchMyAfterSales\(\{ role: 'booster', status: 'pending' \}\)/)
    // 挂在 onMounted 的并行拉取里，不阻塞钱包首屏
    const mounted = slice(walletSource, 'onMounted(() => {', '\n})')
    expect(mounted).toMatch(/fetchAfterSaleGuard\(\)/)
  })

  it('blocks withdrawal while an after-sale is pending and shows the notice', () => {
    const blocked = slice(walletSource, 'const afterSaleWithdrawBlocked = computed(', '\n)')
    expect(blocked).toMatch(/afterSalesStore\.hasPendingBoosterAfterSale/)
    expect(blocked).toMatch(/!authStore\.isAdmin/)
    expect(walletSource).toMatch(/v-if="afterSaleWithdrawBlocked" class="message-warning mt-4">售后处理中，暂不能提现，请等待售后结案/)
    expect(walletSource).toMatch(/:disabled="submittingWithdrawal \|\| walletStore\.submitting \|\| withdrawQuotaNotice\.blocked \|\| afterSaleWithdrawBlocked"/)
  })

  it('raises the same block when the backend rejects the submit with the after-sale copy', () => {
    const submit = slice(walletSource, 'async function submitWithdrawal()', '\n}')
    const failure = submit.slice(submit.indexOf('} else {'))
    expect(failure).toMatch(/withdrawMessage\.value = \{ type: 'error', text: result\.error \|\| '提交失败' \}/)
    expect(failure).toMatch(/if \(\/售后\/\.test\(result\.error \|\| ''\)\)/)
    expect(failure).toMatch(/afterSaleBlockedByServer\.value = true/)
  })
})

describe('管理后台售后管理 tab（AdminView）', () => {
  it('compiles AdminView with the after-sales tab', () => {
    compileView(adminSource, 'AdminView.vue')
  })

  it('registers the afterSales tab key, button and lazy fetch', () => {
    expect(adminSource).toMatch(/const TAB_KEYS = \[[^\]]*'afterSales'[^\]]*\]/)
    expect(adminSource).toMatch(/activeTab === 'afterSales' \? 'tab-pill-active' : 'tab-pill'" @click="activeTab = 'afterSales'">售后管理/)
    const watchBlock = slice(adminSource, 'watch(activeTab, (tab) => {', '\n})')
    expect(watchBlock).toMatch(/if \(tab === 'afterSales'\) \{[\s\S]*?fetchAfterSales\(1\)/)
  })

  it('loads the list through the store with status/order/q filters', () => {
    const fetch = slice(adminSource, 'async function fetchAfterSales(', '\n}')
    expect(fetch).toMatch(/await afterSalesStore\.fetchAdminAfterSales\(\{/)
    expect(fetch).toMatch(/status: afterSaleStatus\.value/)
    expect(fetch).toMatch(/orderId: afterSaleOrderId\.value\.trim\(\) \|\| undefined/)
    expect(fetch).toMatch(/q: afterSaleSearch\.value\.trim\(\) \|\| undefined/)
  })

  it('renders the required columns from the store list', () => {
    const section = slice(adminSource, "v-else-if=\"activeTab === 'afterSales'\"", 'v-else-if="activeTab === \'wallet-adjust\'"')
    // 订单号/标题、老板、打手、售后金额、状态 tag、申请时间
    expect(section).toMatch(/#\{\{ afterSaleOrderLabel\(item\)\.id \}\} \{\{ afterSaleOrderLabel\(item\)\.title \}\}/)
    expect(section).toMatch(/老板 \{\{ afterSaleBossName\(item\) \}\} · 打手 \{\{ afterSaleBoosterName\(item\) \}\}/)
    expect(section).toMatch(/\{\{ formatPrice\(item\.amount\) \}\}/)
    expect(section).toMatch(/getAfterSaleStatusTagClass\(item\.status\)/)
    expect(section).toMatch(/申请于 \{\{ formatDateTime\(item\.created_at\) \}\}/)
    // 原因截断 + 点击看全文
    expect(section).toMatch(/class="truncate text-sm text-ink-2">\{\{ item\.reason \|\| '—' \}\}/)
    expect(section).toMatch(/@click="afterSaleReasonDetail = item"/)
    // 凭证缩略图：点击在站内灯箱打开，不再 window.open / _blank
    // （微信内置浏览器没有标签页，target=_blank / window.open 必跳出站内）
    expect(section).toMatch(/@click\.stop="openAfterSaleLightbox\(item, index\)"/)
    expect(adminSource).toMatch(/openAfterSaleLightbox\(afterSaleReasonDetail, index\)/)
    expect(adminSource).not.toMatch(/openAfterSaleImage/)
    expect(adminSource).not.toMatch(/window\.open\(/)
    expect(adminSource).not.toMatch(/target="_blank"/)
  })

  it('offers 通过/驳回 only for PENDING and reviews through the store', () => {
    const section = slice(adminSource, "v-else-if=\"activeTab === 'afterSales'\"", 'v-else-if="activeTab === \'wallet-adjust\'"')
    expect(section).toMatch(/v-if="item\.status === 'PENDING'"/)
    expect(section).toMatch(/openAfterSaleApproveModal\(item\)/)
    expect(section).toMatch(/openAfterSaleRejectModal\(item\)/)

    const submit = slice(adminSource, 'async function submitModal()', '\n  closeModal()')
    expect(submit).toMatch(/await afterSalesStore\.reviewAfterSale\(state\.afterSaleId, \{/)
    expect(submit).toMatch(/state\.type === 'after-sale-approve' \? 'approve' : 'reject'/)
    expect(submit).toMatch(/await fetchAfterSales\(afterSalesStore\.adminAfterSalesPagination\.page\)/)
  })

  it('requires a reject reason on the frontend like withdrawal rejection', () => {
    const submit = slice(adminSource, 'async function submitModal()', '\n  closeModal()')
    const guard = submit.slice(
      submit.indexOf("state.type === 'after-sale-reject'") - 40,
      submit.indexOf("state.type === 'after-sale-reject'") + 120,
    )
    expect(guard).toMatch(/if \(state\.type === 'after-sale-reject' && !state\.reason\.trim\(\)\)/)
    expect(guard).toMatch(/state\.error = '请填写驳回原因'/)
  })

  it('states the deduction transfer in the approve confirmation', () => {
    expect(adminSource).toMatch(/v-if="modal\.type === 'after-sale-approve'" class="message-warning">\s*通过将从打手账户扣划 \{\{ formatPrice\(modal\.amount\) \}\} 划转给老板/)
    expect(adminSource).toMatch(/if \(modal\.value\?\.type === 'after-sale-approve'\) return '通过售后申请'/)
    expect(adminSource).toMatch(/if \(modal\.value\?\.type === 'after-sale-reject'\) return '驳回售后申请'/)
  })
})
