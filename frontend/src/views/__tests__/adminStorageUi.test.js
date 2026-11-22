/**
 * 后台「对象存储」UI 契约（source-contract 风格，与 orderFlowContracts.test.js 一致）。
 *
 * 覆盖重点：
 * - AdminView 暴露 storage tab、管理员可见、渲染 AdminStorageSettings；
 * - 组件确实走完四个接口（settings / test / migrate / cleanup-local）；
 * - 密钥输入框为 password 且留空提交 "__KEEP__"；
 * - 清理远程文件必须 window.confirm 且被 failed/pending 双重闸门拦住；
 * - 2 秒轮询只在 running=true 时启动，running 变 false / 卸载必须销毁。
 */
import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const adminSource = readFileSync(new URL('../AdminView.vue', import.meta.url), 'utf8')
const componentSource = readFileSync(
  new URL('../../components/admin/AdminStorageSettings.vue', import.meta.url),
  'utf8'
)
const storeSource = readFileSync(new URL('../../stores/adminStorage.js', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

// 取某个函数/脚本片段的完整花括号体，避免只靠正则先后顺序猜测守卫位置。
function functionBody(source, startMarker) {
  const start = source.indexOf(startMarker)
  expect(start).toBeGreaterThan(-1)
  const open = source.indexOf('{', start)
  expect(open).toBeGreaterThan(-1)
  let depth = 0
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1
    else if (source[index] === '}') {
      depth -= 1
      if (depth === 0) return source.slice(start, index + 1)
    }
  }
  throw new Error(`unbalanced body for ${startMarker}`)
}

describe('admin storage tab wiring', () => {
  it('compiles the storage settings component', () => {
    compileView(componentSource, 'AdminStorageSettings.vue')
  })

  it('adds storage to TAB_KEYS so ?tab=storage is a valid deep link', () => {
    const tabKeys = adminSource.match(/const TAB_KEYS = \[[\s\S]*?\]/)
    expect(tabKeys).not.toBeNull()
    expect(tabKeys[0]).toContain("'storage'")
    // 顺序固定在站点管理之后、支付设置之前，与后台导航布局一致
    expect(tabKeys[0].indexOf("'storage'")).toBeGreaterThan(tabKeys[0].indexOf("'site'"))
    expect(tabKeys[0].indexOf("'storage'")).toBeLessThan(tabKeys[0].indexOf("'payment'"))
  })

  it('renders an admin-only 对象存储 tab right after 站点管理', () => {
    const siteTabIndex = adminSource.indexOf('>站点管理</button>')
    const storageTabIndex = adminSource.indexOf('>对象存储</button>')
    expect(siteTabIndex).toBeGreaterThan(-1)
    expect(storageTabIndex).toBeGreaterThan(siteTabIndex)

    const storageButton = adminSource.slice(
      adminSource.lastIndexOf('<button', storageTabIndex),
      storageTabIndex + '>对象存储</button>'.length,
    )
    expect(storageButton).toMatch(/v-if="isAdmin"/)
    expect(storageButton).toMatch(/activeTab === 'storage' \? 'tab-pill-active' : 'tab-pill'/)
    expect(storageButton).toMatch(/@click="activeTab = 'storage'"/)
  })

  it('imports and renders AdminStorageSettings like AdminSiteSettings', () => {
    expect(adminSource).toMatch(
      /import AdminSiteSettings from '@\/components\/admin\/AdminSiteSettings\.vue'\s*\n\s*import AdminStorageSettings from '@\/components\/admin\/AdminStorageSettings\.vue'/
    )
    expect(adminSource).toMatch(
      /<AdminSiteSettings v-else-if="activeTab === 'site'" \/>\s*\n\s*<AdminStorageSettings v-else-if="activeTab === 'storage'" \/>/
    )
  })
})

describe('admin storage component contracts', () => {
  it('talks to all documented storage endpoints through the store', () => {
    const requiredPaths = [
      '/admin/storage/settings',
      '/admin/storage/test',
      '/admin/storage/migrate',
      '/admin/storage/cleanup-local',
    ]
    for (const path of requiredPaths) expect(componentSource).toContain(path)
    // 组件只经 store 发请求；store 里才出现真实 api 调用，避免两处抄 URL
    expect(componentSource).toMatch(/import \{ KEEP_SECRET_PLACEHOLDER, useAdminStorageStore \} from '@\/stores\/adminStorage'/)
    expect(componentSource).not.toMatch(/api\.(get|post|put)\(/)
  })

  it('keeps both credentials as password inputs and sends __KEEP__ when untouched', () => {
    expect(componentSource).toMatch(/id="cos-secret-id"[\s\S]{0,220}type="password"/)
    expect(componentSource).toMatch(/id="cos-secret-key"[\s\S]{0,220}type="password"/)
    // 打码值只能做 placeholder，输入框内容必须清空，不能把掩码当密钥提交
    expect(componentSource).toMatch(/secretIdInput\.value = ''/)
    expect(componentSource).toMatch(/secretKeyInput\.value = ''/)

    const payloadFn = functionBody(componentSource, 'function buildSavePayload()')
    expect(payloadFn).toMatch(/cos_secret_id: secretIdInput\.value\.trim\(\) \|\| KEEP_SECRET_PLACEHOLDER/)
    expect(payloadFn).toMatch(/cos_secret_key: secretKeyInput\.value\.trim\(\) \|\| KEEP_SECRET_PLACEHOLDER/)
    expect(storeSource).toMatch(/export const KEEP_SECRET_PLACEHOLDER = '__KEEP__'/)
  })

  it('never claims success when the backend rejects the settings switch', () => {
    const saveFn = functionBody(componentSource, 'async function save()')
    expect(saveFn).toMatch(/const result = await store\.saveSettings\(buildSavePayload\(\)\)/)
    // 失败分支先 return，成功文案只出现在成功路径
    const failIndex = saveFn.indexOf('if (!result.success)')
    const successIndex = saveFn.indexOf("type: 'success'")
    expect(failIndex).toBeGreaterThan(-1)
    expect(successIndex).toBeGreaterThan(failIndex)
    expect(saveFn.slice(failIndex, successIndex)).toMatch(/type: 'error'[\s\S]*?result\.error[\s\S]*?return/)
    expect(saveFn).toMatch(/text: '站点\/存储设置已保存'/)
  })

  it('shows the backend Chinese message for both test outcomes', () => {
    const testFn = functionBody(componentSource, 'async function testConnection()')
    expect(testFn).toMatch(/store\.testConnection\(buildTestPayload\(\)\)/)
    expect(testFn).toMatch(/result\.data\?\.ok === true/)
    expect(testFn).toMatch(/type: ok \? 'success' : 'error'/)
    expect(testFn).toMatch(/result\.data\?\.message/)
    // 测试期间按钮禁用并显示进度，避免连点打出重复探测
    expect(componentSource).toMatch(/:disabled="testing"/)
    expect(componentSource).toMatch(/\{\{ testing \? '测试中\.\.\.' : '测试连接' \}\}/)
    expect(componentSource).toMatch(/:class="testNotice\.type === 'success' \? 'message-success' : 'message-error'"/)
  })

  it('gates local cleanup behind a confirm dialog and a full-success migration', () => {
    const cleanupFn = functionBody(componentSource, 'async function startCleanup()')
    expect(cleanupFn).toMatch(/window\.confirm\(/)
    expect(cleanupFn).toMatch(/本地不再保留副本，仅 COS 有/)
    expect(cleanupFn).toMatch(/if \(!confirmed\) return/)
    expect(cleanupFn.indexOf('window.confirm')).toBeLessThan(cleanupFn.indexOf('store.startCleanup()'))

    const canCleanup = functionBody(componentSource, 'const canCleanup = computed')
    expect(canCleanup).toMatch(/migrationTotal\.value > 0/)
    expect(canCleanup).toMatch(/migrationDone\.value > 0/)
    expect(canCleanup).toMatch(/migrationFailed\.value === 0/)
    expect(canCleanup).toMatch(/migrationPending\.value === 0/)
    // 迁移在跑 / 清理在跑时都要禁用，不能并发删文件
    expect(canCleanup).toMatch(/!migrationRunning\.value/)
    expect(canCleanup).toMatch(/!cleanupRunning\.value/)
    expect(componentSource).toMatch(/:disabled="!canCleanup \|\| cleanupBusy"/)
    expect(componentSource).toMatch(/清理已迁移的本地文件/)
  })

  it('polls migrate and cleanup status every 2s only while running', () => {
    const startMigrationPolling = functionBody(componentSource, 'function startMigrationPolling()')
    expect(startMigrationPolling).toMatch(/setInterval\(async \(\) => \{[\s\S]*?store\.fetchMigrationStatus\(\)/)
    expect(startMigrationPolling).toMatch(/if \(!result\.success \|\| !store\.migrationStatus\.running\) stopMigrationPolling\(\)/)
    expect(startMigrationPolling).toMatch(/\}, 2000\)/)

    const syncMigrationPolling = functionBody(componentSource, 'function syncMigrationPolling()')
    expect(syncMigrationPolling).toMatch(/if \(store\.migrationStatus\.running\) startMigrationPolling\(\)/)
    expect(syncMigrationPolling).toMatch(/else stopMigrationPolling\(\)/)

    const startCleanupPolling = functionBody(componentSource, 'function startCleanupPolling()')
    expect(startCleanupPolling).toMatch(/store\.fetchCleanupStatus\(\)/)
    expect(startCleanupPolling).toMatch(/if \(!result\.success \|\| !store\.cleanupStatus\.running\) stopCleanupPolling\(\)/)
    expect(startCleanupPolling).toMatch(/\}, 2000\)/)

    // 卸载必须清掉两个轮询定时器（含启动后的单次补检），刷新页面/切 tab 都不留下幽灵请求
    const unmount = functionBody(componentSource, 'onUnmounted(() =>')
    expect(unmount).toMatch(/stopMigrationPolling\(\)/)
    expect(unmount).toMatch(/stopCleanupPolling\(\)/)
    expect(unmount).toMatch(/clearTimeout\(migrationKickTimer\)/)
    expect(unmount).toMatch(/clearTimeout\(cleanupKickTimer\)/)
    expect(componentSource).toMatch(/clearInterval\(migrationTimer\)/)
    expect(componentSource).toMatch(/clearInterval\(cleanupTimer\)/)
  })

  it('renders migration counters, inline-width progress bar and failure hint', () => {
    expect(componentSource).toContain('已迁移')
    expect(componentSource).toMatch(/migrationDone/)
    expect(componentSource).toMatch(/migrationFailed/)
    expect(componentSource).toMatch(/migrationPending/)
    expect(componentSource).toMatch(/:style="\{ width: `\$\{migrationProgress\}%` \}"/)
    expect(componentSource).toMatch(/migrationStatus\.current_path/)
    expect(componentSource).toMatch(/migrationStatus\.last_error[\s\S]{0,80}text-danger/)
    // 失败行下轮重试的提示必须出现（后端重试 attempts<3）
    expect(componentSource).toMatch(/migrationFailed > 0/)
    expect(componentSource).toMatch(/失败可在下轮重试/)
    // 暂停只允许在运行中点击
    expect(componentSource).toMatch(/:disabled="!migrationRunning \|\| migrationBusy"/)
    expect(componentSource).toContain('暂停')
    expect(componentSource).toMatch(/migrationCanResume \? '继续迁移' : '开始迁移'/)
  })

  it('renders the security note and keeps COS config editable-but-muted on LOCAL', () => {
    expect(componentSource).toContain('密钥加密保存在数据库，页面只显示打码后的尾号')
    expect(componentSource).toMatch(/cosMuted \? 'opacity-60' : ''/)
    expect(componentSource).toMatch(/const cosMuted = computed\(\(\) => form\.storage_mode === 'LOCAL'\)/)
    // LOCAL 时不禁用输入：管理员可以先填配置并测试，再切换
    expect(componentSource).not.toMatch(/cos-secret-id[\s\S]{0,200}:disabled/)
    expect(componentSource).toMatch(/本地服务器存储/)
    expect(componentSource).toMatch(/腾讯云对象存储/)
  })
})

describe('admin storage store contracts', () => {
  it('declares the exact admin storage endpoints', () => {
    expect(storeSource).toContain("api.get('/admin/storage/settings')")
    expect(storeSource).toContain("api.put('/admin/storage/settings', payload)")
    expect(storeSource).toContain("api.post('/admin/storage/test', payload)")
    expect(storeSource).toContain("api.post('/admin/storage/migrate')")
    expect(storeSource).toContain("api.get('/admin/storage/migrate/status')")
    expect(storeSource).toContain("api.post('/admin/storage/migrate/pause')")
    expect(storeSource).toContain("api.post('/admin/storage/cleanup-local')")
    expect(storeSource).toContain("api.get('/admin/storage/cleanup/status')")
    expect(storeSource).toMatch(/import api from '@\/utils\/api'/)
  })

  it('surfaces the backend detail for settings failures', () => {
    expect(storeSource).toMatch(/const detail = err\?\.detail \?\? err\?\.original\?\.response\?\.data\?\.detail/)
    expect(storeSource).toMatch(/typeof detail === 'string' && detail/)
    expect(storeSource).toMatch(/return \{ success: false, error: message, detail: detail \?\? null \}/)
  })
})
