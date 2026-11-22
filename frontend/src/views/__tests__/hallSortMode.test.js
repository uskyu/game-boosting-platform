import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')
const adminSettingsSource = readFileSync(new URL('../../components/admin/AdminSiteSettings.vue', import.meta.url), 'utf8')
const ordersStoreSource = readFileSync(new URL('../../stores/orders.js', import.meta.url), 'utf8')
const chatStoreSource = readFileSync(new URL('../../stores/chat.js', import.meta.url), 'utf8')
const siteStoreSource = readFileSync(new URL('../../stores/site.js', import.meta.url), 'utf8')

function slice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing start marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start, end)
}

// ── 后台配置大厅排序 + 大厅/WS 侧跟随排序模式 ──
describe('hall sort mode contracts', () => {
  it('site store defaults to NEWEST and documents the public-hall-only scope', () => {
    expect(siteStoreSource).toMatch(/hall_sort_mode:\s*'NEWEST'/)
    // 中文注释说明只影响大厅公开排序
    expect(siteStoreSource).toMatch(/仅影响公开大厅/)
  })

  it('admin settings form syncs, saves and renders both mode pills', () => {
    // form 默认值 + sync 只认两个已知值
    expect(adminSettingsSource).toMatch(/hall_sort_mode:\s*'NEWEST'/)
    expect(adminSettingsSource).toMatch(
      /form\.hall_sort_mode = store\.settings\.hall_sort_mode === 'OLDEST' \? 'OLDEST' : 'NEWEST'/,
    )

    // 保存 payload 一定带上模式（后端按全量表单保存，缺字段会把排序重置）
    const saveIndex = adminSettingsSource.indexOf('async function save()')
    expect(saveIndex).toBeGreaterThan(-1)
    const saveBlock = adminSettingsSource.slice(saveIndex, adminSettingsSource.indexOf('function addReplacement()', saveIndex))
    expect(saveBlock).toMatch(/hall_sort_mode:\s*form\.hall_sort_mode,/)

    // 两个胶囊按钮文案与点击处理
    expect(adminSettingsSource).toContain('最新发布优先')
    expect(adminSettingsSource).toContain('最早发布优先')
    expect(adminSettingsSource).toMatch(/@click="form\.hall_sort_mode = 'NEWEST'"/)
    expect(adminSettingsSource).toMatch(/@click="form\.hall_sort_mode = 'OLDEST'"/)
    // 复用现有胶囊样式，并按当前模式高亮
    expect(adminSettingsSource).toMatch(/form\.hall_sort_mode === 'NEWEST' \? 'filter-pill-active' : 'filter-pill'/)
    expect(adminSettingsSource).toMatch(/form\.hall_sort_mode === 'OLDEST' \? 'filter-pill-active' : 'filter-pill'/)

    // 说明文案：只影响大厅公开展示
    expect(adminSettingsSource).toMatch(/仅影响大厅公开展示/)
    expect(adminSettingsSource).toMatch(/续单、重发会排到后面/)
  })

  it('hall digest key includes the current sort mode', () => {
    const digestKey = slice(hallSource, 'function hallDigestKey(digest)', '// 429 的 Retry-After')
    expect(digestKey).toMatch(/digest\.earliest_deadline \?\? ''/)
    expect(digestKey).toMatch(/\$\{ordersStore\.hallSortMode\}/)
  })

  it('orders store normalizes mode and inserts new cards on the matching side', () => {
    // 模式只认 NEWEST/OLDEST
    const setter = slice(ordersStoreSource, 'function setHallSortMode(mode)', '// GET /orders/hall-digest')
    expect(setter).toMatch(/hallSortMode\.value = mode === 'OLDEST' \? 'OLDEST' : 'NEWEST'/)

    // 列表 hall 范围下发时更新模式（mine_published 为 null，不能覆盖）
    expect(ordersStoreSource).toMatch(
      /context === 'hall' && response\.data\.hall_sort_mode != null/,
    )
    expect(ordersStoreSource).toMatch(/setHallSortMode\(response\.data\.hall_sort_mode\)/)

    // 摘要探测 / setHallDigest 也能同步模式
    const fetchDigest = slice(ordersStoreSource, 'async function fetchHallDigest(', 'function setOrdersContext(')
    expect(fetchDigest).toMatch(/response\.data\?\.hall_sort_mode != null/)
    const setDigest = slice(ordersStoreSource, 'function setHallDigest(digest)', 'function setHallSortMode(mode)')
    expect(setDigest).toMatch(/if \(digest\.hall_sort_mode != null\) setHallSortMode\(digest\.hall_sort_mode\)/)

    // 本地插卡方向：NEWEST 头部、OLDEST 尾部（与服务端排序一致）
    const applyCard = slice(ordersStoreSource, 'function applyNewOrderCard(card)', 'function applyOrderStateChange(')
    expect(applyCard).toMatch(
      /const items = hallSortMode\.value === 'OLDEST' \? \[\.\.\.cache\.items, card\] : \[card, \.\.\.cache\.items\]/,
    )
    // WS 插卡方向必须与服务端排序一致（注释）
    expect(applyCard).toMatch(/与服务端排序一致/)
  })

  it('chat store handles hall_sort_changed via the order state change machinery', () => {
    const sortCase = slice(chatStoreSource, "case 'hall_sort_changed'", "case 'notification'")
    expect(sortCase).toMatch(/data\.sort_mode === 'OLDEST' \? 'OLDEST' : 'NEWEST'/)
    expect(sortCase).toMatch(/setHallSortMode\(mode\)/)
    expect(sortCase).toMatch(/hall_sort_changed: true/)
    expect(sortCase).toMatch(/orderStateChangeVersion\.value \+= 1/)
  })

  it('hall view refreshes on hall_sort_changed and refetches site settings', () => {
    const stateChangeHandler = slice(
      hallSource,
      'watch(() => chatStore.lastOrderStateChange',
      'watch(() => chatStore.socketStatus',
    )
    expect(stateChangeHandler).toMatch(/if \(change\.hall_sort_changed\)/)
    // 排序变化必须走既有的 silentRefresh 全量刷新路径，并重拉站点设置
    const helper = slice(hallSource, 'function refreshHallAfterSortChange()', '// 新订单通知到达时')
    expect(helper).toMatch(/hallDigestBaseline = null/)
    expect(helper).toMatch(/silentRefresh\(\)\.catch\(\(\) => \{\}\)/)
    expect(helper).toMatch(/siteStore\.fetchSettings\(true\)/)
    expect(stateChangeHandler).toMatch(/refreshHallAfterSortChange\(\)/)
  })
})
