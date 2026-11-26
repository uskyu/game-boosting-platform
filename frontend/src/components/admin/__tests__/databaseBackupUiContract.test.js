/**
 * 后台「数据库容灾备份」UI 契约。
 *
 * 源码层面锁定关键约束，防止回退：
 * - store 只走 admin-only 的备份接口，下载走 gzip blob + Content-Disposition 文件名；
 * - 表单保留后端默认值（60 分钟 / 48 份 / db-backups）与 5–1440、1–500 的输入边界；
 * - 手动备份后每 5 秒轮询 settings + runs，running 变 false 或卸载时清掉定时器；
 * - 对象存储页确实引入并渲染了新卡片。
 */
import { readFileSync } from 'node:fs'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const componentSource = readFileSync(
  new URL('../AdminDatabaseBackup.vue', import.meta.url),
  'utf8',
)
const storeSource = readFileSync(
  new URL('../../../stores/adminBackup.js', import.meta.url),
  'utf8',
)
const storageSettingsSource = readFileSync(
  new URL('../AdminStorageSettings.vue', import.meta.url),
  'utf8',
)

function compileSfc(source, filename) {
  const { descriptor, errors } = parse(source, { filename })
  expect(errors).toEqual([])

  const script = compileScript(descriptor, { id: filename })
  const template = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  })

  expect(template.errors).toEqual([])
}

function functionBody(source, startMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing function: ${startMarker}`).toBeGreaterThan(-1)
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

describe('database backup ui contracts', () => {
  it('compiles the touched SFCs', () => {
    compileSfc(componentSource, 'AdminDatabaseBackup.vue')
    compileSfc(storageSettingsSource, 'AdminStorageSettings.vue')
  })

  it('uses the admin-only backup endpoints and downloads gzip blobs', () => {
    expect(storeSource).toContain("api.get('/admin/backup/settings')")
    expect(storeSource).toContain("api.put('/admin/backup/settings'")
    expect(storeSource).toContain("api.post('/admin/backup/run')")
    expect(storeSource).toContain("api.get('/admin/backup/runs'")
    expect(storeSource).toContain('params: { page: nextPage, page_size: pageSize.value }')
    expect(storeSource).toContain('`/admin/backup/runs/${run.id}/download`')
    expect(storeSource).toContain("responseType: 'blob'")
    expect(storeSource).toContain('parseContentDispositionFilename')
    expect(storeSource).toContain('downloadBlobFile')
    expect(storeSource).toContain("type: 'application/gzip'")
  })

  it('keeps the settings defaults and only submits the five editable fields', () => {
    expect(storeSource).toContain('interval_minutes: 60')
    expect(storeSource).toContain('retention_count: 48')
    expect(storeSource).toContain("prefix: 'db-backups'")
    expect(storeSource).toContain('cos_configured: false')
    expect(storeSource).toContain("storage_mode: 'LOCAL'")
    expect(storeSource).toContain('running: false')
    expect(storeSource).toContain('pageSize = ref(10)')

    expect(componentSource).toContain('interval_minutes: 60')
    expect(componentSource).toContain('retention_count: 48')
    expect(componentSource).toContain("prefix: 'db-backups'")
    expect(componentSource).toContain('include_notifications: false')

    expect(componentSource).toContain('function buildSavePayload()')
    const payloadBody = functionBody(componentSource, 'function buildSavePayload()')
    for (const field of [
      'enabled:',
      'interval_minutes:',
      'retention_count:',
      'prefix:',
      'include_notifications:',
    ]) {
      expect(payloadBody).toContain(field)
    }
    // 只提交这 5 个可编辑字段，服务端状态字段不能混进 PUT body
    expect(payloadBody).not.toContain('cos_configured')
    expect(payloadBody).not.toContain('storage_mode')
    expect(payloadBody).not.toContain('last_backup')
  })

  it('renders the required interval and retention bounds', () => {
    expect(componentSource).toContain('min="5"')
    expect(componentSource).toContain('max="1440"')
    expect(componentSource).toContain('min="1"')
    expect(componentSource).toContain('max="500"')
    expect(componentSource).toContain('5–1440')
    expect(componentSource).toContain('1–500')
    expect(componentSource).toContain('db-backups/cn1')
    expect(componentSource).toContain('db-backups/cn2')
  })

  it('polls settings and runs every 5 seconds and clears the timer on unmount', () => {
    const startBody = functionBody(componentSource, 'function startPolling()')
    expect(startBody).toContain('setInterval')
    expect(startBody).toContain('5000')
    expect(startBody).toContain('store.fetchSettings()')
    expect(startBody).toContain('store.fetchRuns(page.value)')
    expect(startBody).toContain('if (!settingsResult.success) stopPolling()')
    expect(startBody).toContain('else if (!store.settings.running) stopPolling()')

    const stopBody = functionBody(componentSource, 'function stopPolling()')
    expect(stopBody).toContain('clearInterval(pollTimer)')
    expect(stopBody).toContain('pollTimer = null')

    const unmountBody = functionBody(componentSource, 'onUnmounted(')
    expect(unmountBody).toContain('stopPolling()')

    expect(componentSource).toContain('store.requestRun()')
    expect(componentSource).toContain('startPolling()')
  })

  it('offers downloads only for successful runs through the store', () => {
    expect(componentSource).toContain("run.status === 'SUCCESS'")
    expect(componentSource).toContain('store.downloadRun(run)')
    expect(componentSource).toContain('下载')
  })

  it('keeps the status prompts, empty state, and overflow-safe layout', () => {
    expect(componentSource).toContain('数据库容灾备份')
    expect(componentSource).toContain('请先在对象存储中配置腾讯云 COS 后再启用备份')
    expect(componentSource).toContain('备份执行中...')
    expect(componentSource).toContain('暂无备份记录')
    expect(componentSource).toContain('overflow-x-auto')
    expect(componentSource).toContain('sm:grid-cols-2')
    expect(componentSource).toContain('<CompactPagination')
    expect(storeSource).toContain('page_size: pageSize.value')
    expect(componentSource).toContain('runs.value.slice(0, pageSize.value)')
  })

  it('is imported and rendered by the storage settings page', () => {
    expect(storageSettingsSource).toContain(
      "import AdminDatabaseBackup from '@/components/admin/AdminDatabaseBackup.vue'",
    )
    expect(storageSettingsSource).toContain('<AdminDatabaseBackup class="mt-4" />')
    // 原有安全提示必须保留，只允许在页面末尾追加新卡片
    expect(storageSettingsSource).toContain('密钥加密保存在数据库，页面只显示打码后的尾号。')
  })
})
