import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { beforeEach, describe, expect, it } from 'vitest'

const source = readFileSync(new URL('../OrderList.vue', import.meta.url), 'utf8')

// vitest 默认 node 环境没有 localStorage：用最小内存实现顶替，
// 只测「读-写-回落」这套契约本身，不依赖真实浏览器存储。
function makeStorage() {
  const map = new Map()
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    clear: () => map.clear(),
  }
}

let storage

function compile() {
  const { descriptor, errors } = parse(source, { filename: 'OrderList.vue' })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id: 'OrderList.vue' })).not.toThrow()
}

describe('我的订单：记住上次打开的 Tab（老板 2026-10-19）', () => {
  beforeEach(() => {
    storage = makeStorage()
  })

  it('compiles the view', () => {
    compile()
  })

  it('defines a stable localStorage key for the tab', () => {
    expect(source).toMatch(/ORDER_TAB_STORAGE_KEY\s*=\s*'gb\.orders\.lastTab'/)
  })

  it('reads the remembered tab when the URL has no tab query', () => {
    // 从导航栏 / 「返回列表」进来是不带 query 的：必须先从本地存储恢复
    const init = source.slice(
      source.indexOf('const initialQueryTab'),
      source.indexOf('let userTouchedTab')
    )
    expect(init).toMatch(/route\.query\.tab/)
    expect(init).toMatch(/readRememberedTab\(\)/)
    // 优先级：URL 显式指定 > 上次记忆 > 默认 claims
    expect(init).toMatch(/initialQueryTab \|\| rememberedAtMount \|\| 'claims'/)
    expect(init).toMatch(/const rememberedAtMount = initialQueryTab \? null : readRememberedTab\(\)/)
  })

  it('only accepts the two real tabs from storage', () => {
    // 存了脏值（旧版本/手改）不能把页面搞成空白态
    const reader = source.slice(
      source.indexOf('function readRememberedTab'),
      source.indexOf('function rememberTab')
    )
    expect(reader).toMatch(/saved === 'claims' \|\| saved === 'published'/)
    expect(reader).toMatch(/return null/)
  })

  it('survives localStorage being unavailable (隐私模式)', () => {
    const reader = source.slice(
      source.indexOf('function readRememberedTab'),
      source.indexOf('function rememberTab')
    )
    expect(reader).toMatch(/try\s*\{/)
    expect(reader).toMatch(/catch/)
    const writer = source.slice(
      source.indexOf('function rememberTab'),
      source.indexOf('const initialQueryTab')
    )
    expect(writer).toMatch(/try\s*\{/)
    expect(writer).toMatch(/catch/)
  })

  it('remembers on every tab change path (click / URL restore / admin default)', () => {
    // switchTab 里记一次
    const switchTab = source.slice(
      source.indexOf('function switchTab'),
      source.indexOf('function applyAdminDefaultTab')
    )
    expect(switchTab).toMatch(/rememberTab\(tab\)/)

    // syncQuery 是所有切换路径的汇合点，也记一次
    const syncQuery = source.slice(
      source.indexOf('function syncQuery'),
      source.indexOf('// 浏览器前进/后退只改 query')
    )
    expect(syncQuery).toMatch(/rememberTab\(activeTab\.value\)/)

    // 前进/后退恢复 URL 里的 tab 时也要记
    expect(source).toMatch(/if \(qTab\) rememberTab\(qTab\)/)
  })

  it('admin default never overrides an explicit remembered choice', () => {
    // 管理员没记忆时才默认「我的派单」；上次自己选过就尊重选择
    const adminWatch = source.slice(
      source.indexOf('watch(() => authStore.isAdmin'),
      source.indexOf('}, { immediate: true })', source.indexOf('watch(() => authStore.isAdmin'))
    )
    expect(adminWatch).toMatch(/rememberedAtMount/)
  })

  it('does not break the existing URL-query behaviour', () => {
    // URL 传了 tab 时仍然以 URL 为准（分享链接/后退必须能定位到指定栏）
    expect(source).toMatch(/route\.query\.tab === 'claims' \|\| route\.query\.tab === 'published'/)
    expect(source).toMatch(/tab: activeTab\.value/)
  })
})

describe('记住 Tab 的行为模拟', () => {
  const KEY = 'gb.orders.lastTab'

  // 与 OrderList.vue 里的 readRememberedTab / rememberTab 同契约
  function read() {
    const saved = storage.getItem(KEY)
    return saved === 'claims' || saved === 'published' ? saved : null
  }
  function write(tab) {
    if (tab === 'claims' || tab === 'published') storage.setItem(KEY, tab)
  }

  beforeEach(() => {
    storage = makeStorage()
  })

  it('works like the view contract: 上次派单 → 下次进派单', () => {
    // 第一次进来没记录 → 回落 claims
    expect(read() || 'claims').toBe('claims')

    // 用户点了「我的派单」
    write('published')
    expect(read()).toBe('published')

    // 离开页面再回来（无 query）→ 恢复 published
    expect(read() || 'claims').toBe('published')

    // 切回「我的接单」
    write('claims')
    expect(read() || 'claims').toBe('claims')
  })

  it('ignores tampered values and falls back to claims', () => {
    storage.setItem(KEY, 'somethingElse')
    expect(read() || 'claims').toBe('claims')
  })

  it('ignores invalid writes', () => {
    write('bogus')
    expect(read()).toBeNull()
  })
})
