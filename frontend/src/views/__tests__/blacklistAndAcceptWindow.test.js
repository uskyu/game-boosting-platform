import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const detailSource = readFileSync(new URL('../OrderDetail.vue', import.meta.url), 'utf8')
const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')
const listSource = readFileSync(new URL('../OrderList.vue', import.meta.url), 'utf8')
const profileSource = readFileSync(new URL('../ProfileView.vue', import.meta.url), 'utf8')
const blacklistViewSource = readFileSync(new URL('../BlacklistView.vue', import.meta.url), 'utf8')
const pickModalSource = readFileSync(new URL('../../components/BlacklistPickModal.vue', import.meta.url), 'utf8')
const confirmModalSource = readFileSync(new URL('../../components/BlacklistConfirmModal.vue', import.meta.url), 'utf8')
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

describe('等待时间 pending + 被拉黑（OrderDetail）', () => {
  it('compiles the detail view with the new viewer flags', () => {
    compileView(detailSource, 'OrderDetail.vue')
  })

  it('maps accept_blocked / accept_window_pending from the order response', () => {
    expect(detailSource).toMatch(/const acceptBlocked = computed\(\(\) => Boolean\(order\.value\?\.accept_blocked\)\)/)
    expect(detailSource).toMatch(/order\.value\?\.accept_blocked_reason \|\| '你已被该发布人拉黑，无法接此单'/)
    expect(detailSource).toMatch(/const acceptWindowPending = computed\(\(\) => Boolean\(order\.value\?\.accept_window_pending\)\)/)
  })

  it('canAcceptOrder refuses blocked orders and pending never renders as 无需等待', () => {
    const canAccept = slice(detailSource, 'const canAcceptOrder = computed(', '// ── 抢单倒计时')
    expect(canAccept).toMatch(/isOwner\.value \|\| hasClaimed\.value \|\| acceptBlocked\.value/)
    // pending 分支必须在 else（无需等待）之前
    const waitNoticeStart = detailSource.indexOf('v-if="isBooster && canAccept && canAcceptOrder"')
    expect(waitNoticeStart).toBeGreaterThan(-1)
    const waitNotice = detailSource.slice(waitNoticeStart, waitNoticeStart + 1600)
    expect(waitNotice).toMatch(/acceptWaitState === 'pending'[\s\S]{0,120}正在获取接单等待时间…/)
    expect(waitNotice.indexOf("acceptWaitState === 'pending'")).toBeLessThan(waitNotice.indexOf('接单无需等待'))
  })

  it('shows the blocked warning with the server-provided reason and disables the claim entry', () => {
    expect(detailSource).toMatch(/v-if="isBooster && !isOwner && acceptBlocked"[\s\S]{0,200}{{ acceptBlockedReason }}/)
    // pending / 详情加载中 / 被拉黑都不能点
    expect(detailSource).toMatch(/acceptWindowPending \|\| detailLoading \|\| acceptBlocked/)
    // pending 的按钮文案也要说明在加载，而不是伪装成可接
    expect(detailSource).toMatch(/if \(acceptWindowPending\.value\) return '等待时间加载中…'/)
  })

  it('rechecks blocked and wait state after the preflight before opening the captcha modal', () => {
    const fn = slice(detailSource, 'async function openClaimModal()', '\nfunction closeClaimFailModal()')

    // 入口两道闸：先沿用原有等待/在飞锁，再拦被拉黑/pending
    expect(fn).toMatch(/if \(hasClaimed\.value \|\| actionLoading\.value \|\| claimPreflightLoading\.value \|\| acceptWaitSeconds\.value > 0\)/)
    expect(fn).toMatch(/if \(acceptBlocked\.value \|\| acceptWindowPending\.value\)/)

    const preflightIndex = fn.indexOf('fresh = await ordersStore.fetchOrder(')
    expect(preflightIndex).toBeGreaterThan(-1)
    const blockedIndex = fn.indexOf('if (acceptBlocked.value)', preflightIndex)
    const waitIndex = fn.indexOf('if (acceptWindowPending.value || acceptWaitSeconds.value > 0)', preflightIndex)
    const openIndex = fn.indexOf('showClaimModal.value = true', preflightIndex)
    expect(blockedIndex).toBeGreaterThan(preflightIndex)
    expect(waitIndex).toBeGreaterThan(blockedIndex)
    expect(openIndex).toBeGreaterThan(waitIndex)

    // 预检后发现仍在等待：提示等待，不打开验证码弹窗
    const waitBlock = fn.slice(waitIndex, openIndex)
    expect(waitBlock).toMatch(/接单还需等待/)
    expect(waitBlock).toMatch(/正在获取接单等待时间/)
    expect(waitBlock).not.toMatch(/showClaimModal\.value = true/)
    // 预检后发现被拉黑：展示服务端固定文案
    expect(fn.slice(blockedIndex, waitIndex)).toMatch(/errorMessage\.value = acceptBlockedReason\.value/)
  })

  it('keeps the server 400 「还需等待 N 秒」 local supplement', () => {
    const handler = slice(detailSource, 'async function handleConfirmClaim()', '\nfunction openDeliverModal()')
    expect(handler).toMatch(/const waitSeconds = parseAcceptWaitSeconds\(result\.error\)/)
    expect(handler).toMatch(/acceptGateOverrideUntil\.value = serverNow\(\) \+ waitSeconds \* 1000/)
    expect(detailSource).toMatch(/function parseAcceptWaitSeconds\(message\)/)
  })

  it('adds a 拉黑 action to every claim row and wires the shared confirmation modal', () => {
    expect(detailSource).toMatch(/blacklistStore\.isBlocked\(claim\.booster_id\)/)
    expect(detailSource).toMatch(/@click\.stop="openBlacklistConfirm\(claim\)"/)
    expect(detailSource).toMatch(/function openBlacklistConfirm\(claim\)/)
    expect(detailSource).toMatch(/<BlacklistConfirmModal[\s\S]{0,200}v-model="showBlacklistConfirm"/)
    expect(detailSource).toMatch(/@blocked="onBlacklistBlocked"/)
    // 本地标记已拉黑，不刷新整页
    expect(detailSource).toMatch(/function onBlacklistBlocked\(userId\)/)
  })
})

describe('大厅卡片：拉黑徽标与等待加载中', () => {
  it('compiles the hall view', () => {
    compileView(hallSource, 'OrderHallView.vue')
  })

  it('keeps blocked orders clickable but shows the 发单人已限制 badge', () => {
    expect(hallSource).toMatch(/order\.accept_blocked[^>]*>发单人已限制</)
    // 卡片整体 onClick 仍在：被拉黑用户依然可以点进详情
    expect(hallSource).toMatch(/@click="goToOrder\(order\.id\)"/)
  })

  it('renders 等待时间加载中 for pending and keeps the countdown for remaining > 0', () => {
    const waitView = slice(hallSource, 'function getAcceptWaitView(', 'const orders = computed')
    expect(waitView).toMatch(/meta\.state === 'pending'/)
    expect(waitView).toMatch(/label: '等待时间加载中'/)
    expect(waitView).not.toMatch(/无需等待/)
    expect(hallSource).toMatch(/getAcceptWaitView\(order\)\.pending/)
    expect(hallSource).toMatch(/getAcceptWaitView\(order\)\.label/)
    // 有剩余秒数时仍按原倒计时展示
    expect(hallSource).toMatch(/接单等待：剩余 \$\{remaining\} 秒（配置等待 \$\{total\} 秒）/)
    // 被拉黑的卡片不显示倒计时
    const metaFor = slice(hallSource, 'function getAcceptWaitMetaFor(', 'function getAcceptWaitLabel')
    expect(metaFor).toMatch(/order\.accept_blocked/)
  })
})

describe('我的派单：快捷拉黑入口与弹窗', () => {
  it('compiles OrderList with the pick modal', () => {
    compileView(listSource, 'OrderList.vue')
  })

  it('only shows 拉黑 for published orders with claims, and stops click-through', () => {
    expect(listSource).toMatch(/import BlacklistPickModal from '@\/components\/BlacklistPickModal\.vue'/)
    const actions = slice(listSource, '<div class="mt-3 flex flex-wrap justify-end gap-2">', '重建订单')
    expect(actions).toMatch(/v-if="canPublish && Number\(order\.claimed_count \?\? 0\) > 0"/)
    expect(actions).toMatch(/@click\.stop="openBlacklistPick\(order\)"/)
    expect(actions.indexOf('拉黑')).toBeLessThan(actions.indexOf('openBlacklistPick'))
  })

  it('mounts the modal with the order id and title (loaded on demand)', () => {
    expect(listSource).toMatch(/const blacklistPickOpen = ref\(false\)/)
    expect(listSource).toMatch(/function openBlacklistPick\(order\)/)
    expect(listSource).toMatch(/<BlacklistPickModal[\s\S]{0,220}:order-id="blacklistPickOrderId"/)
    expect(listSource).toMatch(/<BlacklistPickModal[\s\S]{0,260}:order-title="blacklistPickOrderTitle"/)
  })

  it('compiles the pick modal and only lists that order\'s claimants', () => {
    compileView(pickModalSource, 'BlacklistPickModal.vue')
    expect(pickModalSource).toMatch(/api\.get\(`\/orders\/\$\{props\.orderId\}\/claims`\)/)
    expect(pickModalSource).toMatch(/claim\?\.booster_nickname/)
    expect(pickModalSource).toMatch(/#{{ claim\.booster_id }}/)
    expect(pickModalSource).toMatch(/formatDateTime\(claim\.created_at\)/)
    expect(pickModalSource).toMatch(/getClaimStatusMeta\(claim\?\.status\)/)
    // 不请求全站用户列表/模糊搜索
    expect(pickModalSource).not.toMatch(/\/users/)
    expect(pickModalSource).not.toMatch(/blacklistStore\.lookup/)
    expect(pickModalSource).not.toMatch(/refreshOrdersContext|fetchOrders\(/)
  })

  it('shows 已拉黑 for known blocked users and reuses the shared confirmation copy', () => {
    expect(pickModalSource).toMatch(/blacklistStore\.isBlocked\(claim\.booster_id\)/)
    expect(pickModalSource).toMatch(/>已拉黑</)
    expect(pickModalSource).toMatch(/BLACKLIST_BLOCK_NOTICE/)
    expect(pickModalSource).toMatch(/emit\('blocked'/)
    // 成功后只在本地标记（store.add 记 id），不重刷整页
    expect(pickModalSource).toMatch(/await blacklistStore\.add\(userId\)/)
  })
})

describe('黑名单页 / Profile 入口 / 路由', () => {
  it('compiles the blacklist page and confirmation modal', () => {
    compileView(blacklistViewSource, 'BlacklistView.vue')
    compileView(confirmModalSource, 'BlacklistConfirmModal.vue')
  })

  it('only looks up one exact username/email and never renders a candidate list', () => {
    expect(blacklistViewSource).toMatch(/用户名 \/ 登录邮箱（需完全一致）/)
    expect(blacklistViewSource).toMatch(/blacklistStore\.lookup\(lookupQuery\.value\)/)
    expect(blacklistViewSource).toMatch(/const lookupUser = computed\(\(\) => blacklistStore\.lookupResult\)/)
    expect(blacklistViewSource).not.toMatch(/\/users/)
    // 命中后只有单个用户卡片（没有任何 v-for 候选列表）
    expect(blacklistViewSource).not.toMatch(/v-for="user in lookup/)
  })

  it('adds a user with optional reason and removes with confirmation', () => {
    expect(blacklistViewSource).toMatch(/blacklistStore\.add\(target\.id, addReason\.value\)/)
    expect(blacklistViewSource).toMatch(/blacklistStore\.remove\(item\.blocked_user_id\)/)
    expect(blacklistViewSource).toMatch(/window\.confirm\(`确定解除对/)
    expect(blacklistViewSource).toMatch(/备注（选填，仅你自己可见）/)
  })

  it('has loading/empty/error/pagination states and only loads for publishers', () => {
    expect(blacklistViewSource).toMatch(/v-if="listLoading && !items\.length"/)
    expect(blacklistViewSource).toMatch(/v-else-if="items\.length"/)
    expect(blacklistViewSource).toMatch(/黑名单为空/)
    expect(blacklistViewSource).toMatch(/listError/)
    expect(blacklistViewSource).toMatch(/pagination\.pages > 1/)
    expect(blacklistViewSource).toMatch(/if \(canPublish\.value\) fetchPage\(1\)/)
    expect(blacklistViewSource).toMatch(/v-if="!canPublish" class="message-warning"/)
  })

  it('shows the Profile shortcut only for canPublish users', () => {
    compileView(profileSource, 'ProfileView.vue')
    expect(profileSource).toMatch(/v-if="authStore\.canPublish"[\s\S]{0,120}to="\/blacklist"[\s\S]{0,40}黑名单/)
  })

  it('registers /blacklist as an auth-required route', () => {
    expect(routerSource).toMatch(/path: '\/blacklist'/)
    expect(routerSource).toMatch(/name: 'blacklist'/)
    expect(routerSource).toMatch(/meta: \{ title: '黑名单', requiresAuth: true \}/)
    expect(routerSource).toMatch(/import\('@\/views\/BlacklistView\.vue'\)/)
  })
})
