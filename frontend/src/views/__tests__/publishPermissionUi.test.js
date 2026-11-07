import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

function read(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

const adminSource = read('../../views/AdminView.vue')
const siteSettingsSource = read('../../components/admin/AdminSiteSettings.vue')
const adminApplicationsSource = read('../../components/admin/AdminPublishApplications.vue')
const userListSource = read('../../components/admin/AdminUserList.vue')
const hallSource = read('../../views/OrderHallView.vue')
const listSource = read('../../views/OrderList.vue')
const detailSource = read('../../views/OrderDetail.vue')
const createSource = read('../../views/OrderCreate.vue')
const registerSource = read('../../views/RegisterView.vue')
const profileSource = read('../../views/ProfileView.vue')
const permissionViewSource = read('../../views/PublishPermissionView.vue')
const routerSource = read('../../router/index.js')
const authStoreSource = read('../../stores/auth.js')
const siteStoreSource = read('../../stores/site.js')
const adminUsersStoreSource = read('../../stores/adminUsers.js')
const permissionStoreSource = read('../../stores/publishPermission.js')
const notificationsStoreSource = read('../../stores/notifications.js')

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

describe('发单权限 UI 契约', () => {
  it('compiles every changed and new view/component', () => {
    compileView(adminSource, 'AdminView.vue')
    compileView(siteSettingsSource, 'AdminSiteSettings.vue')
    compileView(adminApplicationsSource, 'AdminPublishApplications.vue')
    compileView(userListSource, 'AdminUserList.vue')
    compileView(hallSource, 'OrderHallView.vue')
    compileView(listSource, 'OrderList.vue')
    compileView(detailSource, 'OrderDetail.vue')
    compileView(createSource, 'OrderCreate.vue')
    compileView(registerSource, 'RegisterView.vue')
    compileView(profileSource, 'ProfileView.vue')
    compileView(permissionViewSource, 'PublishPermissionView.vue')
  })

  it('registers the 发单申请 admin tab after 公告管理', () => {
    expect(adminSource).toMatch(/'publishRequests'/)
    expect(adminSource).toMatch(/import AdminPublishApplications from '@\/components\/admin\/AdminPublishApplications\.vue'/)

    const announcementsIndex = adminSource.indexOf("activeTab === 'announcements'")
    const publishTabIndex = adminSource.indexOf("activeTab === 'publishRequests'")
    expect(publishTabIndex).toBeGreaterThan(announcementsIndex)

    const tabButton = adminSource.slice(publishTabIndex - 100, publishTabIndex + 120)
    expect(tabButton).toMatch(/v-if="isAdmin"/)
    expect(tabButton).toMatch(/发单申请/)

    expect(adminSource).toMatch(/<AdminPublishApplications v-else-if="activeTab === 'publishRequests'" \/>/)
    // 原有公告/支付记录 section 顺序不动
    expect(adminSource).toMatch(/<AdminAnnouncements v-else-if="activeTab === 'announcements'" \/>/)
  })

  it('adds the three site settings fields to defaults, sync and save payload', () => {
    for (const field of [
      'registration_default_can_publish',
      'registration_default_can_accept',
      'publish_permission_apply_enabled',
    ]) {
      expect(siteStoreSource).toContain(`${field}: true`)
      expect(siteSettingsSource).toContain(field)
    }
    expect(siteSettingsSource).toMatch(/form\.registration_default_can_publish = store\.settings\.registration_default_can_publish !== false/)
    expect(siteSettingsSource).toMatch(/form\.registration_default_can_accept = store\.settings\.registration_default_can_accept !== false/)
    expect(siteSettingsSource).toMatch(/form\.publish_permission_apply_enabled = store\.settings\.publish_permission_apply_enabled !== false/)
    // 布尔 false 必须显式进保存 payload（不能仅在 true 时带上）
    const saveBlock = slice(siteSettingsSource, 'const r = await store.updateSettings({', 'function addReplacement')
    expect(saveBlock).toMatch(/registration_default_can_publish: form\.registration_default_can_publish,/)
    expect(saveBlock).toMatch(/registration_default_can_accept: form\.registration_default_can_accept,/)
    expect(saveBlock).toMatch(/publish_permission_apply_enabled: form\.publish_permission_apply_enabled,/)
  })

  it('blocks saving site settings until they loaded successfully', () => {
    expect(siteSettingsSource).toMatch(/const settingsLoaded = ref\(false\)/)
    expect(siteSettingsSource).toMatch(/const result = await store\.fetchSettings\(true\)/)
    expect(siteSettingsSource).toMatch(/if \(!result\.success\) \{[\s\S]{0,200}notice\.value/)
    expect(siteSettingsSource).toMatch(/if \(!settingsLoaded\.value\) \{[\s\S]{0,120}站点设置尚未加载成功，请刷新后重试/)
    expect((siteSettingsSource.match(/settingsLoaded\.value = true/g) || []).length).toBeGreaterThanOrEqual(2)
  })

  it('renders the 注册权限 card with three toggle pills and required copy', () => {
    expect(siteSettingsSource).toMatch(/注册权限/)
    expect(siteSettingsSource).toMatch(/只影响注册后的新用户，老用户权限保持不变；管理员发单不受这里限制/)
    // 自助申请开关与注册默认值无关，单独说明会立即影响现有用户
    expect(siteSettingsSource).toMatch(/立即对所有无发单权限的现有用户生效；关闭后用户只能联系管理员开通/)
    expect(siteSettingsSource).toMatch(/新注册用户默认允许发单/)
    expect(siteSettingsSource).toMatch(/新注册用户默认允许接单/)
    expect(siteSettingsSource).toMatch(/开启发单权限自助申请/)
    expect(siteSettingsSource).toMatch(/关闭后用户只能联系管理员开通/)
    expect(siteSettingsSource).toMatch(/:class="form\.registration_default_can_publish \? 'filter-pill-active' : 'filter-pill'"/)
    expect(siteSettingsSource).toMatch(/:class="form\.registration_default_can_accept \? 'filter-pill-active' : 'filter-pill'"/)
    expect(siteSettingsSource).toMatch(/:class="form\.publish_permission_apply_enabled \? 'filter-pill-active' : 'filter-pill'"/)
    expect(siteSettingsSource).toMatch(/:aria-pressed="form\.registration_default_can_publish"/)
    expect(siteSettingsSource).toMatch(/:aria-pressed="form\.registration_default_can_accept"/)
    expect(siteSettingsSource).toMatch(/:aria-pressed="form\.publish_permission_apply_enabled"/)
  })

  it('exposes default-open canPublish/canAccept computed in the auth store', () => {
    expect(authStoreSource).toMatch(/const canPublish = computed\(\(\) => isAdmin\.value \|\| user\.value\?\.can_publish !== false\)/)
    expect(authStoreSource).toMatch(/const canAccept = computed\(\(\) => isAdmin\.value \|\| user\.value\?\.can_accept !== false\)/)
    expect(authStoreSource).toMatch(/^\s*canPublish,\s*$/m)
    expect(authStoreSource).toMatch(/^\s*canAccept,\s*$/m)
  })

  it('registers /publish-permission and blocks order-create until the user is loaded', () => {
    expect(routerSource).toMatch(/path: '\/publish-permission'/)
    expect(routerSource).toMatch(/name: 'publish-permission'/)
    expect(routerSource).toMatch(/component: \(\) => import\('@\/views\/PublishPermissionView\.vue'\)/)
    expect(routerSource).toMatch(/meta: \{ title: '发单权限', requiresAuth: true \}/)

    expect(routerSource).toMatch(/meta: \{ title: '发布订单', requiresAuth: true, requiresPublishPermission: true \}/)

    // 冷加载分支必须把 requiresPublishPermission 放进阻塞等待名单
    expect(routerSource).toMatch(/to\.meta\.adminOnly \|\| to\.meta\.guest \|\| to\.meta\.requiresPublishPermission/)
    const guard = slice(routerSource, 'if (to.meta.requiresPublishPermission) {', 'if (to.meta.guest && authStore.isAuthenticated)')
    expect(guard).toMatch(/await authStore\.fetchCurrentUser\(\)/)
    expect(guard).toMatch(/authStore\.canPublish/)
    expect(guard).toMatch(/next\(\{ name: 'publish-permission', query: \{ redirect: to\.fullPath \} \}\)/)
  })

  it('gates the hall publish entry and shows apply/contact guidance', () => {
    expect(hallSource).toMatch(/const canPublish = computed\(\(\) => authStore\.canPublish\)/)
    expect(hallSource).toMatch(/const publishApplyEnabled = computed\(\(\) => siteStore\.settings\.publish_permission_apply_enabled !== false\)/)
    expect(hallSource).toMatch(/v-if="isAuthenticated && canPublish"[\s\S]{0,200}发布订单/)
    expect(hallSource).toMatch(/v-else-if="isAuthenticated && publishApplyEnabled"[\s\S]{0,220}申请发单权限/)
    expect(hallSource).toMatch(/发单权限未开通，请联系管理员/)
    expect(hallSource).toMatch(/:to="\{ name: 'publish-permission' \}"/)
  })

  it('gates the my-orders publish entry, rebuild/repeat buttons and empty copy', () => {
    expect(listSource).toMatch(/const canPublish = computed\(\(\) => authStore\.canPublish\)/)
    expect(listSource).toMatch(/const isAuthenticated = computed\(\(\) => authStore\.isAuthenticated\)/)
    expect(listSource).toMatch(/v-if="isAuthenticated && canPublish"[\s\S]{0,160}发布订单/)
    expect(listSource).toMatch(/v-else-if="isAuthenticated && publishApplyEnabled"[\s\S]{0,200}申请发单权限/)

    // 空状态：无权限时不再提示点发布，改为申请/联系管理员
    const emptyCopy = slice(listSource, 'const publishedEmptyCopy = computed', 'function getOrderUnreadCount')
    expect(emptyCopy).toMatch(/authStore\.canPublish/)
    expect(emptyCopy).toMatch(/if \(!authStore\.canPublish\) \{[\s\S]*?申请发单权限[\s\S]*?请联系管理员/)
    // 有权限时的「点右上角发布订单」提示仍在，但必须位于无权限分支之后
    expect(emptyCopy.indexOf('if (!authStore.canPublish)'))
      .toBeLessThan(emptyCopy.indexOf('点右上角「发布订单」'))

    // 重建/续单仅在有权限时渲染
    const rebuildBlock = slice(listSource, "v-for=\"order in publishedOrders\"", '</article>')
    expect(rebuildBlock).toMatch(/v-if="canPublish"[\s\S]{0,120}rebuildOrder\(order\.id\)/)
    expect(rebuildBlock).toMatch(/v-if="canPublish"[\s\S]{0,120}repeatOrder\(order\.id\)/)
    // 列表上方有统一的申请/联系管理员引导
    expect(listSource).toMatch(/v-if="!canPublish"[\s\S]{0,500}申请发单权限/)
  })

  it('gates order-detail rebuild/repeat and the accept button', () => {
    expect(detailSource).toMatch(/const canPublish = computed\(\(\) => authStore\.canPublish\)/)
    expect(detailSource).toMatch(/const canAccept = computed\(\(\) => authStore\.canAccept\)/)
    expect(detailSource).toMatch(/v-if="isBooster && canAccept && canAcceptOrder"/)
    expect(detailSource).toMatch(/v-if="isBooster && canAccept && \(canAcceptOrder/)
    expect(detailSource).toMatch(/接单权限未开通/)
    expect(detailSource).toMatch(/v-if="isOwner && canPublish"[\s\S]{0,120}rebuildOrder\(order\.id\)/)
    expect(detailSource).toMatch(/v-if="isOwner && canPublish"[\s\S]{0,120}repeatOrder\(order\.id\)/)
    expect(detailSource).toMatch(/v-if="isOwner && !canPublish && publishApplyEnabled"[\s\S]{0,160}publish-permission/)
    expect(detailSource).toMatch(/v-else-if="isOwner && !canPublish"[\s\S]{0,120}发单权限未开通/)
  })

  it('blocks the create page without touching the existing form canPublish', () => {
    // 现有测试的源码切片里不能混入新 computed
    const existingCanPublish = createSource.slice(
      createSource.indexOf('const canPublish'),
      createSource.indexOf('function handleGameChange'),
    )
    expect(existingCanPublish).not.toMatch(/hasPublishPermission/)
    expect(createSource).toMatch(/const hasPublishPermission = computed\(\(\) => authStore\.canPublish\)/)

    // 原来的禁用表达式必须保持原样
    expect(createSource).toMatch(/:disabled="isSubmitting \|\| !canPublish \|\| createdOrderUploadFailed"/)

    // onMounted 在拉取游戏/模板/服务费前就返回
    const mounted = slice(createSource, 'onMounted(async () => {', '</script>')
    const guardIndex = mounted.indexOf('if (!hasPublishPermission.value) return')
    expect(guardIndex).toBeGreaterThan(-1)
    expect(guardIndex).toBeLessThan(mounted.indexOf('gamesStore.ensureCatalog()'))
    expect(guardIndex).toBeLessThan(mounted.indexOf('serviceFeeStore.fetchSettings(true)'))
    expect(guardIndex).toBeLessThan(mounted.indexOf('prefillFromOrder(recycleId, mode)'))

    // 模板阻止卡片
    expect(createSource).toMatch(/<template v-if="hasPublishPermission">/)
    expect(createSource).toMatch(/<section v-else class="hero-panel[\s\S]{0,400}暂无发单权限/)
    expect(createSource).toMatch(/申请发单权限/)
    expect(createSource).toMatch(/返回大厅/)
    expect(createSource).toMatch(/请联系管理员开通发单权限/)
  })

  it('switches the register copy based on both registration defaults and the apply switch', () => {
    expect(registerSource).toMatch(/const defaultMeta = computed\(\(\) => \{/)
    expect(registerSource).toMatch(/siteStore\.settings\.registration_default_can_publish !== false/)
    expect(registerSource).toMatch(/siteStore\.settings\.registration_default_can_accept !== false/)
    expect(registerSource).toMatch(/注册后就能发布需求、管理订单和进入聊天。/)
    // 发单开 + 接单关：不再承诺注册后即可接单
    expect(registerSource).toMatch(/注册后即可发布需求；接单权限需联系管理员开通。/)
    // 发单关 + 接单开：保留自助申请/联系管理员分支
    expect(registerSource).toMatch(/注册后即可接单；发单权限可\$\{applyEnabled \? '在个人中心自助申请' : '联系管理员开通'\}。/)
    // 发单关 + 接单关：两项都要说明
    expect(registerSource).toMatch(/发单与接单默认都未开通；发单可\$\{applyEnabled \? '在个人中心自助申请' : '联系管理员开通'\}，接单请联系管理员。/)
    expect(registerSource).toMatch(/<p class="text-sm text-ink-2">\{\{ defaultMeta \}\}<\/p>/)
    // 旧的硬编码可见段落已移除
    expect(registerSource).not.toMatch(/<p class="text-sm text-ink-2">注册后就能发布需求/)
  })

  it('adds a 发单权限 shortcut to the profile for non-admins', () => {
    expect(profileSource).toMatch(/v-if="user\?\.role !== 'ADMIN'" to="\/publish-permission" class="btn-secondary !px-4">发单权限<\/router-link>/)
  })

  it('adds server-side permission filters and batch publish actions to user management', () => {
    expect(adminUsersStoreSource).toMatch(/can_publish: options\.can_publish/)
    expect(adminUsersStoreSource).toMatch(/can_accept: options\.can_accept/)
    expect(adminUsersStoreSource).toMatch(/async function bulkPublishPermission\(payload\)/)
    expect(adminUsersStoreSource).toMatch(/\/admin\/users\/bulk-publish-permission/)
    // 全部普通用户操作前先取预览人数（失败不阻断）
    expect(adminUsersStoreSource).toMatch(/async function fetchBulkPreview\(\)/)
    expect(adminUsersStoreSource).toMatch(/api\.get\('\/admin\/users\/bulk-publish-permission\/preview'\)/)
    expect(userListSource).toMatch(/await store\.fetchBulkPreview\(\)/)
    expect(userListSource).toMatch(/eligible: null/)
    expect(userListSource).toMatch(/pending: null/)
    expect(userListSource).toMatch(/previewLoading: scope === 'all'/)
    expect(userListSource).toMatch(/将对全部 <span class="font-semibold text-ink-1">\{\{ bulkModal\.eligible \}\}<\/span> 名启用中的普通用户/)
    expect(userListSource).toMatch(/名用户的待审核申请将一并自动通过（不逐人发送通知）/)
    expect(userListSource).toMatch(/人数获取失败，操作仍会作用于全部启用中的普通用户/)
    expect(userListSource).toMatch(/bulkModal\.can_publish && bulkModal\.pending > 0/)
    // 提交防重复仍在预览/确认之后
    expect(userListSource).toMatch(/if \(!state \|\| state\.submitting\) return[\s\S]{0,200}state\.submitting = true/)

    expect(userListSource).toMatch(/const selectedUserIds = ref\(\[\]\)/)
    expect(userListSource).toMatch(/can_publish: filters\.restriction === 'no_publish' \? false : undefined/)
    expect(userListSource).toMatch(/can_accept: filters\.restriction === 'no_accept' \? false : undefined/)
    // 不再用本地过滤替代服务端分页
    expect(userListSource).not.toMatch(/const visibleUsers = computed/)
    expect(userListSource).toMatch(/selectedUserIds\.value = \[\]/)
    expect(userListSource).toMatch(/function isSelectable\(user\) \{\s*return user\.role !== 'ADMIN'/)
    expect(userListSource).toMatch(/全选本页/)
    expect(userListSource).toMatch(/批量禁发单/)
    expect(userListSource).toMatch(/批量解禁发单/)
    expect(userListSource).toMatch(/全部禁发单/)
    expect(userListSource).toMatch(/只影响启用中的普通用户，管理员不受影响/)
    expect(userListSource).toMatch(/payload\.scope = 'selected'/)
    expect(userListSource).toMatch(/payload\.scope = 'all_non_admin'/)
    expect(userListSource).toMatch(/can_publish: state\.can_publish/)
    expect(userListSource).toMatch(/store\.bulkPublishPermission\(payload\)/)
    // 桌面表格与移动卡片都有选择框
    expect((userListSource.match(/type="checkbox"/g) || []).length).toBeGreaterThanOrEqual(4)
  })

  it('wires the admin application review component', () => {
    expect(adminApplicationsSource).toMatch(/const statusFilter = ref\('PENDING'\)/)
    expect(adminApplicationsSource).toMatch(/PUBLISH_PERMISSION_STATUS_OPTIONS/)
    expect(adminApplicationsSource).toMatch(/item\?\.username/)
    expect(adminApplicationsSource).toMatch(/item\.email/)
    expect(adminApplicationsSource).toMatch(/item\.user_id/)
    expect(adminApplicationsSource).toMatch(/item\.user_created_at/)
    expect(adminApplicationsSource).toMatch(/item\.reason/)
    expect(adminApplicationsSource).toMatch(/item\.created_at/)
    expect(adminApplicationsSource).toMatch(/item\.history_count/)
    expect(adminApplicationsSource).toMatch(/function openApprove\(item\)/)
    expect(adminApplicationsSource).toMatch(/function openReject\(item\)/)
    expect(adminApplicationsSource).toMatch(/state\.type === 'reject' && !reason/)
    expect(adminApplicationsSource).toMatch(/请填写驳回原因/)
    expect(adminApplicationsSource).toMatch(/maxlength="500"/)
    expect(adminApplicationsSource).toMatch(/action: state\.type === 'approve' \? 'approve' : 'reject'/)
    expect(adminApplicationsSource).toMatch(/store\.reviewRequest\(state\.item\.id/)
    expect(adminApplicationsSource).toMatch(/await load\(pagination\.value\.page\)/)

    // 通过文案：只开发单权限，不动接单权限与历史订单；并说明用户端自动刷新
    expect(adminApplicationsSource).toMatch(/通过后仅开通该用户的发单权限，不改动接单权限与历史订单。/)
    expect(adminApplicationsSource).toMatch(/通过后仅开通发单权限，不改动接单权限与历史订单。用户端会自动刷新权限/)
    expect(adminApplicationsSource).toMatch(/若按钮未更新可到「发单权限」页点「刷新权限」/)

    // 换号/进入后台时先清空上一管理员残留，并立即置 loading
    expect(adminApplicationsSource).toMatch(/store\.reset\(\)/)
    expect(adminApplicationsSource).toMatch(/store\.loading = true/)
    const applicationsMounted = slice(adminApplicationsSource, 'onMounted(() => {', 'watch(statusFilter')
    expect(applicationsMounted.indexOf('store.reset()'))
      .toBeLessThan(applicationsMounted.indexOf('load(1)'))

    // 审核后当前页超出总页数时回落到最后一页；pages=0 不回落
    const loadSource = slice(adminApplicationsSource, 'async function load(page = 1) {', 'function handleSearch')
    expect(loadSource).toMatch(/if \(!result\.success\) return/)
    expect(loadSource).toMatch(/if \(page > 1 && pages > 0 && page > pages\)/)
    expect(loadSource).toMatch(/await load\(pages\)/)
  })

  it('implements the four-state self-service application page', () => {
    expect(permissionViewSource).toMatch(/await Promise\.all\(\[authStore\.fetchCurrentUser\(\), loadMine\(\)\]\)/)
    expect(permissionViewSource).toMatch(/store\.fetchMine\(\)/)
    expect(permissionViewSource).toMatch(/const isPending = computed/)
    expect(permissionViewSource).toMatch(/store\.mine\.has_pending/)
    expect(permissionViewSource).toMatch(/v-if="canPublish"[\s\S]{0,160}发单权限已开通/)
    expect(permissionViewSource).toMatch(/v-if="isPending"/)
    expect(permissionViewSource).toMatch(/撤回申请/)
    expect(permissionViewSource).toMatch(/重新申请发单权限/)
    expect(permissionViewSource).toMatch(/未开启自助申请/)
    expect(permissionViewSource).toMatch(/maxlength="500"/)
    expect(permissionViewSource).toMatch(/store\.apply\(reason\.value\)/)
    expect(permissionViewSource).toMatch(/store\.withdraw\(latest\.value\.id\)/)
    expect(permissionViewSource).toMatch(/刷新权限/)
    // 撤回二次确认
    expect(permissionViewSource).toMatch(/if \(!confirmWithdraw\.value\) \{\s*confirmWithdraw\.value = true\s*return\s*\}/)

    // redirect 只接受站内单斜杠路径，// 与反斜杠一律忽略
    expect(permissionViewSource).toMatch(/const redirectTarget = computed\(\(\) => \{/)
    expect(permissionViewSource).toMatch(/value\.startsWith\('\/'\)/)
    expect(permissionViewSource).toMatch(/value\.startsWith\('\/\/'\)/)
    expect(permissionViewSource).toMatch(/value\.includes\('\\\\'\)/)
    expect(permissionViewSource).toMatch(/:to="redirectTarget \|\| \{ name: 'order-create' \}"/)
    expect(permissionViewSource).toMatch(/继续发布订单/)
    expect(permissionViewSource).toMatch(/开通后可继续访问刚才的页面/)

    // 刷新权限：两个请求都成功才提示成功
    const refreshSource = slice(permissionViewSource, 'async function refreshPermission()', 'async function submitApply')
    expect(refreshSource).toMatch(/Promise\.all\(\[loadMine\(\), authStore\.fetchCurrentUser\(\)\]\)/)
    expect(refreshSource).toMatch(/mineResult\?\.success && userResult\?\.success/)
    expect(refreshSource).toMatch(/刷新失败，请稍后重试/)
    expect(permissionViewSource).toMatch(/if \(result\.success\) loaded\.value = true\s*return result/)

    // 换号残留清理 + 接单权限文案分支
    expect(permissionViewSource).toMatch(/store\.reset\(\)/)
    expect(permissionViewSource).toMatch(/authStore\.canAccept \? '审核通过前仍可接单，暂不能发单。' : '接单权限也未开通，请联系管理员；审核通过前暂不能发单。'/)
  })

  it('keeps the frozen publish-permission store endpoints and result shape', () => {
    expect(permissionStoreSource).toMatch(/api\.get\('\/publish-permission\/mine'\)/)
    expect(permissionStoreSource).toMatch(/api\.post\('\/publish-permission\/apply', body\)/)
    expect(permissionStoreSource).toMatch(/api\.post\(`\/publish-permission\/\$\{id\}\/withdraw`\)/)
    expect(permissionStoreSource).toMatch(/api\.get\('\/admin\/publish-permission\/requests', \{ params \}\)/)
    expect(permissionStoreSource).toMatch(/api\.post\(`\/admin\/publish-permission\/requests\/\$\{id\}\/review`, body\)/)
    expect(permissionStoreSource).toMatch(/return \{ success: false, error: err\.message \}/)

    // 换号清理：清空 mine、后台列表、分页与 loading/submitting/error
    const resetSource = slice(permissionStoreSource, 'function reset() {', '// POST /admin/publish-permission/requests/{id}/review')
    expect(resetSource).toMatch(/mine\.value = \{ \.\.\.emptyMine \}/)
    expect(resetSource).toMatch(/adminRequests\.value = \[\]/)
    expect(resetSource).toMatch(/adminPagination\.value = \{ page: 1, pageSize: 20, total: 0, pages: 0 \}/)
    expect(resetSource).toMatch(/loading\.value = false/)
    expect(resetSource).toMatch(/submitting\.value = false/)
    expect(resetSource).toMatch(/error\.value = null/)
  })

  it('refreshes auth and publish-permission state for review result notifications only', () => {
    const refreshSource = slice(
      notificationsStoreSource,
      'function refreshPublishPermissionAfterReview(payload) {',
      'function handleRealtimeNotification(payload) {',
    )
    expect(refreshSource).toMatch(/payload\?\.type !== 'APPLICATION_APPROVED' && payload\?\.type !== 'APPLICATION_REJECTED'/)
    expect(refreshSource).toMatch(/payload\?\.link !== '\/publish-permission'/)
    expect(refreshSource).toMatch(/authStore\.fetchCurrentUser\(\)\.catch\(\(\) => \{\}\)/)
    expect(refreshSource).toMatch(/usePublishPermissionStore\(\)\.fetchMine\(\)\.catch\(\(\) => \{\}\)/)
    // 只有成功接收的新通知才触发刷新（重复通知提前 return）
    const handler = slice(notificationsStoreSource, 'function handleRealtimeNotification(payload) {', 'function resetState()')
    expect(handler.indexOf('refreshPublishPermissionAfterReview(payload)'))
      .toBeGreaterThan(handler.indexOf('receivedIds.add(id)'))
    expect(handler.indexOf('refreshPublishPermissionAfterReview(payload)'))
      .toBeGreaterThan(handler.indexOf('return false'))
  })

  it('shows a contact-admin empty state in 我的接单 when can_accept is false', () => {
    const claimsEmpty = slice(listSource, 'const claimsEmptyCopy = computed', 'const hasPublishedFilters')
    expect(claimsEmpty).toMatch(/if \(authStore\.canAccept === false\) \{/)
    expect(claimsEmpty).toMatch(/接单权限未开通，请联系管理员开通后再来接单。/)
    expect(claimsEmpty).toMatch(/去大厅看看，有合适的单子直接接。/)
    // 无权限分支必须在默认文案之前
    expect(claimsEmpty.indexOf('authStore.canAccept === false'))
      .toBeLessThan(claimsEmpty.indexOf('去大厅看看'))
  })
})
