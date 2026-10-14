import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const createSource = readFileSync(new URL('../OrderCreate.vue', import.meta.url), 'utf8')
const detailSource = readFileSync(new URL('../OrderDetail.vue', import.meta.url), 'utf8')
const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')
const listSource = readFileSync(new URL('../OrderList.vue', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('order-flow view contracts', () => {
  it('compiles both changed Vue views', () => {
    compileView(createSource, 'OrderCreate.vue')
    compileView(detailSource, 'OrderDetail.vue')
  })

  it('keeps publish disabled across upload and delayed navigation', () => {
    expect(createSource).toMatch(/:disabled="isSubmitting\s*\|\|\s*!canPublish\s*\|\|\s*createdOrderUploadFailed"/)
    expect(createSource).toMatch(/const isSubmitting = computed\(\(\) => submitting\.value \|\| recycleLoading\.value\)/)
    expect(createSource).toMatch(/submitStage\.value = `图片上传中 \$\{index \+ 1\}\/\$\{selected\.length\}`/)
    expect(createSource).toMatch(/await new Promise\(\(resolve\) => window\.setTimeout\(resolve, 900\)\)\s*await router\.push\(/)
    expect(createSource).toMatch(/finally\s*\{\s*submitting\.value = false/)
  })

  it('does not upload after a failed create and keeps file uploads sequential', () => {
    const createIndex = createSource.indexOf('const result = await measureOrderFlowStage(')
    const createFailureIndex = createSource.indexOf('if (!result.success)', createIndex)
    const uploadIndex = createSource.indexOf("'attachment_uploads'", createIndex)
    expect(createIndex).toBeGreaterThan(-1)
    expect(createFailureIndex).toBeGreaterThan(createIndex)
    expect(uploadIndex).toBeGreaterThan(createFailureIndex)
    expect(createSource.slice(createFailureIndex, uploadIndex)).toMatch(/return/)
    expect(createSource).toMatch(/for \(let index = 0; index < selected\.length; index \+= 1\)[\s\S]*?await api\.post\(/)
    expect(createSource).not.toMatch(/Promise\.all\([^)]*uploadAttachments/)
    expect(createSource).toMatch(/createdOrderUploadFailed\.value = true[\s\S]*?请勿重新发布同一订单/)
    expect(createSource).toMatch(/v-if="createdOrderUploadFailed"[\s\S]{0,300}查看已创建订单/)
    expect(createSource).toMatch(/:disabled="isSubmitting \|\| !canPublish \|\| createdOrderUploadFailed"/)
  })

  it('keeps cached hall and published orders visible while they refresh', () => {
    expect(hallSource).toMatch(/v-if="loading && isAuthenticated && !hasHallOrders"/)
    expect(listSource).toMatch(/v-if="loading && !hasPublishedOrders"/)
    expect(hallSource).toMatch(/:aria-busy="loading"/)
    expect(listSource).toMatch(/:aria-busy="loading"/)
    expect(hallSource).toMatch(/refreshOrdersContext\('hall'/)
    expect(listSource).toMatch(/refreshOrdersContext\('minePublished'/)
  })

  it('surfaces chat-start failures next to the chat button on terminal orders', () => {
    // 终态订单按钮保持可点（后端已允许重开已有会话），失败时就近展示错误，
    // 否则用户只看到按钮闪一下"打开中..."，错误藏在页面顶部的 errorMessage 里。
    expect(detailSource).toContain('const chatError = ref(\'\')')

    const handlerStart = detailSource.indexOf('async function handleStartConversation()')
    const handlerEnd = detailSource.indexOf('async function fetchReviews(', handlerStart)
    const handler = detailSource.slice(handlerStart, handlerEnd)
    expect(handler).toMatch(/chatError\.value = ''/)
    expect(handler).toMatch(/chatError\.value = (result\.error|err\?\.message)/)
    // 顶部横幅必须照旧工作
    expect(handler).toMatch(/errorMessage\.value = result\.error/)
    expect(handler).toMatch(/errorMessage\.value = err\?\.message/)

    const buttonIndex = detailSource.indexOf("联系打手' : '联系老板'")
    expect(buttonIndex).toBeGreaterThan(-1)
    expect(detailSource).toMatch(/联系打手[\s\S]{0,400}v-if="chatError"/)
    expect(detailSource.slice(buttonIndex, buttonIndex + 400)).toMatch(/v-if="chatError"/)
    expect(detailSource).toMatch(/<p v-if="chatError" class="message-error mt-2 text-xs">\{\{ chatError \}\}<\/p>/)
  })

  it('silently hydrates order details after successful accept and always clears action loading', () => {
    const handlerStart = detailSource.indexOf('async function handleConfirmClaim()')
    const handlerEnd = detailSource.indexOf('\nfunction openDeliverModal()', handlerStart)
    const handler = detailSource.slice(handlerStart, handlerEnd)
    expect(handler).toMatch(/if \(result\.success\)[\s\S]*?ordersStore\.fetchOrder\(orderId, \{ silent: true \}\)/)
    expect(handler).toMatch(/await loadClaims\(\)/)
    expect(handler).toMatch(/let acceptSucceeded = false[\s\S]*?if \(acceptSucceeded\)[\s\S]*?已接手订单，但订单信息刷新失败/)
    expect(handler).toMatch(/catch \{[\s\S]*?errorMessage\.value = '接单失败，请稍后重试'/)
    expect(handler).toMatch(/finally\s*\{\s*actionLoading\.value = false/)
  })

  it('locks recycle prefill with a local recycleLoading flag instead of shared store loading', () => {
    // 预填原单期间禁用发布栏：锁必须是页面本地的，不能借用（更不能写入）
    // 共享的 orders store loading —— 它归列表刷新所有，互锁会连带禁用无关按钮。
    expect(createSource).not.toMatch(/ordersStore\.loading/)
    expect(createSource).toMatch(/const recycleLoading = ref\(false\)/)

    const prefillStart = createSource.indexOf('async function prefillFromOrder(')
    const prefillEnd = createSource.indexOf('\nasync function publishOrder(', prefillStart)
    expect(prefillStart).toBeGreaterThan(-1)
    expect(prefillEnd).toBeGreaterThan(prefillStart)
    const prefill = createSource.slice(prefillStart, prefillEnd)

    const lockIndex = prefill.indexOf('recycleLoading.value = true')
    const fetchIndex = prefill.indexOf('ordersStore.fetchOrder(orderId)')
    const unlockIndex = prefill.indexOf('recycleLoading.value = false')
    expect(lockIndex).toBeGreaterThan(-1)
    expect(fetchIndex).toBeGreaterThan(lockIndex)
    expect(unlockIndex).toBeGreaterThan(fetchIndex)
  })
})

// 与上面「order-flow view contracts」隔离：只覆盖管理员身份晚到时的默认 tab 契约。
describe('order list admin late-auth defaults', () => {
  it('switches a late-arriving admin to minePublished with exactly one fetch', () => {
    expect(listSource).toMatch(/watch\(\(\) => authStore\.isAdmin, \(isAdmin, wasAdmin\) => \{[\s\S]*?\}, \{ immediate: true \}\)/)

    const watchStart = listSource.indexOf('watch(() => authStore.isAdmin')
    const watchEnd = listSource.indexOf('}, { immediate: true })', watchStart)
    expect(watchStart).toBeGreaterThan(-1)
    expect(watchEnd).toBeGreaterThan(watchStart)
    const adminWatch = listSource.slice(watchStart, watchEnd)

    // URL 指定 tab / 用户手动点过 tab 时一律让位；非管理员不动
    expect(adminWatch).toMatch(/if \(!isAdmin \|\| userTouchedTab \|\| route\.query\.tab\) \{\s*return\s*\}/)

    // 初始管理员：只切默认 tab，watch 自身不发请求（首屏请求在 onMounted，避免重复）
    const initialBranchStart = adminWatch.indexOf('wasAdmin === undefined')
    const lateBranchStart = adminWatch.indexOf('wasAdmin === false')
    expect(initialBranchStart).toBeGreaterThan(-1)
    expect(lateBranchStart).toBeGreaterThan(initialBranchStart)
    const initialBranch = adminWatch.slice(initialBranchStart, lateBranchStart)
    expect(initialBranch).toMatch(/activeTab\.value = 'published'/)
    expect(initialBranch).toMatch(/syncQuery\(\)/)
    expect(initialBranch).toMatch(/return/)
    expect(initialBranch).not.toMatch(/fetchOrders/)
    expect(initialBranch).not.toMatch(/applyAdminDefaultTab/)
    expect(adminWatch).not.toMatch(/fetchOrders/)

    // 身份在挂载前晚到：先对齐 tab/URL，由 onMounted 唯一拉首屏，避免双请求。
    expect(listSource).toMatch(/let listMounted = false/)
    const lateBranch = adminWatch.slice(lateBranchStart)
    expect(lateBranch).toMatch(/if \(!listMounted\) \{[\s\S]*?syncQuery\(\)[\s\S]*?return[\s\S]*?\}/)
    expect(lateBranch).toMatch(/applyAdminDefaultTab\(\)/)
    expect(listSource).toMatch(/onMounted\(async \(\) => \{\s*\n\s*listMounted = true/)

    const helperStart = listSource.indexOf('function applyAdminDefaultTab()')
    const helperEnd = listSource.indexOf('\n}', helperStart)
    expect(helperStart).toBeGreaterThan(-1)
    expect(helperEnd).toBeGreaterThan(helperStart)
    const helper = listSource.slice(helperStart, helperEnd)
    expect(helper).toMatch(/activeTab\.value = 'published'/)
    expect(helper).toMatch(/ordersStore\.setFilters\(publishedCacheFilters\.value\)/)
    expect(helper).toMatch(/ordersStore\.setOrdersContext\('minePublished'/)
    expect(helper).toMatch(/ordersStore\.setPage\(1\)/)
    expect(helper.match(/fetchOrders\(/g)).toHaveLength(1)
    // context/filters 先就位再请求，列表请求不会读到旧的 claims 上下文
    expect(helper.indexOf("setOrdersContext('minePublished'")).toBeLessThan(helper.indexOf('fetchOrders()'))
    expect(helper).toMatch(/syncQuery\(1\)/)

    // 初始管理员的首屏请求仍由 onMounted 负责
    expect(listSource).toMatch(/activeTab\.value === 'published' \? fetchOrders\(\) : Promise\.resolve\(\)/)
  })
})

// ── 大厅刷新契约（isolated block：只覆盖 OrderHallView 的大厅刷新路径，不改上方原测试）──
describe('hall refresh contracts', () => {
  function hallSlice(startMarker, endMarker) {
    const start = hallSource.indexOf(startMarker)
    expect(start).toBeGreaterThan(-1)
    const end = hallSource.indexOf(endMarker, start)
    expect(end).toBeGreaterThan(start)
    return hallSource.slice(start, end)
  }

  const stateChangeHandler = hallSlice(
    'watch(() => chatStore.lastOrderStateChange',
    'watch(() => chatStore.socketStatus',
  )

  it('patches orders already in the hall cache locally without a full refresh', () => {
    expect(stateChangeHandler).toMatch(/if \(!change\) return/)
    expect(stateChangeHandler).toMatch(
      /const knownInHall = orders\.value\.some\(\(order\) => Number\(order\.id\) === Number\(change\.order_id\)\)/,
    )
    expect(stateChangeHandler).toMatch(/ordersStore\.applyOrderStateChange\(change\)/)
    expect(stateChangeHandler).toMatch(/if \(knownInHall\) return\s*\n\s*silentRefresh\(\)\.catch\(\(\) => \{\}\)/)
    // 该 watcher 里只允许一次网络刷新，且必须发生在本地 patch 之后
    expect((stateChangeHandler.match(/silentRefresh\(\)/g) || []).length).toBe(1)
    expect(stateChangeHandler.indexOf('ordersStore.applyOrderStateChange(change)'))
      .toBeLessThan(stateChangeHandler.indexOf('silentRefresh()'))
    // 不得绕过 silentRefresh 直接全量刷新列表
    expect(stateChangeHandler).not.toMatch(/refreshOrdersContext/)
  })

  it('still refreshes for unknown or newly visible orders', () => {
    // knownInHall 只是「跳过」条件：未知订单（可能新增可见订单）仍走 silentRefresh 兜底
    const gateIndex = stateChangeHandler.indexOf('if (knownInHall) return')
    const refreshIndex = stateChangeHandler.indexOf('silentRefresh()')
    expect(gateIndex).toBeGreaterThan(-1)
    expect(refreshIndex).toBeGreaterThan(gateIndex)
  })

  it('keeps the notification-triggered immediate refresh without debounce', () => {
    const notificationHandler = hallSlice(
      'watch(() => notificationsStore.newOrderNotificationVersion',
      'watch(() => chatStore.lastOrderStateChange',
    )
    expect(notificationHandler).toMatch(/silentRefresh\(\)\.catch\(\(\) => \{\}\)/)
    expect((notificationHandler.match(/silentRefresh\(\)/g) || []).length).toBe(1)
    expect(notificationHandler).not.toMatch(/setTimeout|clearTimeout|knownInHall/)
  })

  it('keeps the hall reconcile and fallback intervals unchanged', () => {
    expect(hallSource).toMatch(/const HALL_RECONCILE_INTERVAL = 10_000/)
    expect(hallSource).toMatch(/const HALL_FALLBACK_INTERVAL = 2_000/)
    expect(hallSource).toMatch(
      /return chatStore\.socketStatus === 'connected'\s*\n\s*\? HALL_RECONCILE_INTERVAL\s*\n\s*: HALL_FALLBACK_INTERVAL/,
    )
  })

  it('merges focus and visibilitychange recovery into one debounced refresh', () => {
    const visibilityHandler = hallSlice(
      'function handleHallVisibility()',
      'async function silentRefresh()',
    )
    // 时钟校准与轮询重启保持即时（本地、零请求）
    expect(visibilityHandler).toMatch(/now\.value = serverNow\(\)/)
    expect(visibilityHandler).toMatch(/restartHallTimer\(\)/)

    // 唯一的网络刷新必须包在短 debounce 里，且先清掉同次恢复排入的旧定时器
    expect((visibilityHandler.match(/silentRefresh\(\)/g) || []).length).toBe(1)
    const clearIndex = visibilityHandler.indexOf('clearTimeout(hallResumeRefreshTimer)')
    const timeoutIndex = visibilityHandler.indexOf('window.setTimeout(')
    const refreshIndex = visibilityHandler.indexOf('silentRefresh()')
    expect(clearIndex).toBeGreaterThan(-1)
    expect(timeoutIndex).toBeGreaterThan(clearIndex)
    expect(refreshIndex).toBeGreaterThan(timeoutIndex)
    expect(visibilityHandler).toMatch(/\}, HALL_RESUME_REFRESH_DEBOUNCE\)/)

    const debounceMatch = hallSource.match(/const HALL_RESUME_REFRESH_DEBOUNCE = ([\d_]+)/)
    expect(debounceMatch).not.toBeNull()
    const debounceMs = Number(debounceMatch[1].replace(/_/g, ''))
    expect(debounceMs).toBeGreaterThan(0)
    expect(debounceMs).toBeLessThanOrEqual(500)

    // focus 与 visibilitychange 两个入口继续指向同一个合并函数
    expect(hallSource).toMatch(/document\.addEventListener\('visibilitychange', handleHallVisibility\)/)
    expect(hallSource).toMatch(/window\.addEventListener\('focus', handleHallVisibility\)/)

    // 卸载时必须清掉待执行的 debounce，避免卸载后复活刷新
    const unmountBlock = hallSlice('onUnmounted(() => {', '</script>')
    expect(unmountBlock).toMatch(/clearTimeout\(hallResumeRefreshTimer\)/)
  })
})
