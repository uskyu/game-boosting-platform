import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const apiGet = vi.fn()
const apiPost = vi.fn()
const apiPut = vi.fn()
vi.mock('@/utils/api', () => ({
  default: {
    get: (...args) => apiGet(...args),
    post: (...args) => apiPost(...args),
    put: (...args) => apiPut(...args),
  },
}))

import { KEEP_SECRET_PLACEHOLDER, useAdminStorageStore } from '@/stores/adminStorage'

let pinia

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
  pinia = createPinia()
  setActivePinia(pinia)
})

function makeSettings(overrides = {}) {
  return {
    storage_mode: 'COS',
    cos_bucket: 'game-1314124236',
    cos_region: 'ap-chongqing',
    cos_cdn_domain: '',
    cos_secret_id_masked: '****abcd',
    cos_secret_key_masked: '****wxyz',
    cos_configured: true,
    ...overrides,
  }
}

describe('adminStorage store', () => {
  it('fetchSettings GETs /admin/storage/settings and normalizes the snapshot', async () => {
    apiGet.mockResolvedValueOnce({ data: makeSettings() })
    const store = useAdminStorageStore()

    const result = await store.fetchSettings()

    expect(apiGet).toHaveBeenCalledWith('/admin/storage/settings')
    expect(result.success).toBe(true)
    expect(store.settings.storage_mode).toBe('COS')
    expect(store.settings.cos_configured).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('saveSettings PUTs the payload and replaces local settings', async () => {
    apiPut.mockResolvedValueOnce({ data: makeSettings({ storage_mode: 'LOCAL', cos_configured: false }) })
    const store = useAdminStorageStore()

    const payload = {
      storage_mode: 'LOCAL',
      cos_secret_id: KEEP_SECRET_PLACEHOLDER,
      cos_secret_key: KEEP_SECRET_PLACEHOLDER,
      cos_bucket: '',
      cos_region: '',
      cos_cdn_domain: '',
    }
    const result = await store.saveSettings(payload)

    expect(apiPut).toHaveBeenCalledWith('/admin/storage/settings', payload)
    expect(result.success).toBe(true)
    expect(store.settings.storage_mode).toBe('LOCAL')
  })

  it('saveSettings surfaces the backend Chinese detail and does not touch settings on 400', async () => {
    const store = useAdminStorageStore()
    store.settings = makeSettings({ storage_mode: 'LOCAL' })
    apiPut.mockRejectedValueOnce({ status: 400, message: '密钥无效，切换已取消' })

    const result = await store.saveSettings({ storage_mode: 'COS', cos_secret_id: 'x', cos_secret_key: 'y' })

    expect(result.success).toBe(false)
    expect(result.error).toBe('密钥无效，切换已取消')
    expect(store.error).toBe('密钥无效，切换已取消')
    // 后端未落库：本地不能假装切到了 COS
    expect(store.settings.storage_mode).toBe('LOCAL')
  })

  it('saveSettings prefers err.detail over the generic message', async () => {
    const store = useAdminStorageStore()
    apiPut.mockRejectedValueOnce({ status: 400, message: '请求失败', detail: 'Bucket 与 Region 不匹配' })

    const result = await store.saveSettings({ storage_mode: 'COS' })

    expect(result.success).toBe(false)
    expect(result.error).toBe('Bucket 与 Region 不匹配')
    expect(result.detail).toBe('Bucket 与 Region 不匹配')
  })

  it('testConnection POSTs to /admin/storage/test and returns ok + message', async () => {
    apiPost.mockResolvedValueOnce({ data: { ok: false, message: '无法访问 Bucket：签名校验失败' } })
    const store = useAdminStorageStore()

    const result = await store.testConnection({ cos_bucket: 'b', cos_region: 'r' })

    expect(apiPost).toHaveBeenCalledWith('/admin/storage/test', { cos_bucket: 'b', cos_region: 'r' })
    expect(result.success).toBe(true)
    expect(result.data).toEqual({ ok: false, message: '无法访问 Bucket：签名校验失败' })
  })

  it('testConnection failure still returns a readable error', async () => {
    apiPost.mockRejectedValueOnce({ status: 500, message: '服务器内部错误' })
    const store = useAdminStorageStore()

    const result = await store.testConnection()

    expect(result.success).toBe(false)
    expect(result.error).toBe('服务器内部错误')
  })

  it('startMigration POSTs /migrate then refreshes the status snapshot', async () => {
    apiPost.mockResolvedValueOnce({ data: { ok: true } })
    apiGet.mockResolvedValueOnce({
      data: { total: 12, done: 3, failed: 1, pending: 8, running: true, paused: false, current_path: 'a.jpg', last_error: '', local_files: 12, local_bytes: 2048 },
    })
    const store = useAdminStorageStore()

    const result = await store.startMigration()

    expect(apiPost).toHaveBeenCalledWith('/admin/storage/migrate')
    expect(apiGet).toHaveBeenCalledWith('/admin/storage/migrate/status')
    expect(result.success).toBe(true)
    expect(store.migrationStatus).toMatchObject({ total: 12, done: 3, failed: 1, pending: 8, running: true, current_path: 'a.jpg' })
  })

  it('pauseMigration POSTs /migrate/pause and clears running', async () => {
    apiPost.mockResolvedValueOnce({ data: { ok: true } })
    apiGet.mockResolvedValueOnce({ data: { total: 12, done: 3, failed: 0, pending: 9, running: false, paused: true } })
    const store = useAdminStorageStore()

    const result = await store.pauseMigration()

    expect(apiPost).toHaveBeenCalledWith('/admin/storage/migrate/pause')
    expect(result.success).toBe(true)
    expect(store.migrationStatus.running).toBe(false)
    expect(store.migrationStatus.paused).toBe(true)
  })

  it('fetchMigrationStatus and fetchCleanupStatus read their status endpoints', async () => {
    apiGet
      .mockResolvedValueOnce({ data: { total: 5, done: 5, failed: 0, pending: 0, running: false, paused: false, local_files: 5, local_bytes: 10 } })
      .mockResolvedValueOnce({ data: { running: false, deleted: 4, failed: 1, remaining: 1, last_error: '权限不足' } })
    const store = useAdminStorageStore()

    await store.fetchMigrationStatus()
    const result = await store.fetchCleanupStatus()

    expect(apiGet).toHaveBeenNthCalledWith(1, '/admin/storage/migrate/status')
    expect(apiGet).toHaveBeenNthCalledWith(2, '/admin/storage/cleanup/status')
    expect(store.migrationStatus.done).toBe(5)
    expect(result.data).toMatchObject({ deleted: 4, failed: 1, remaining: 1, last_error: '权限不足' })
  })

  it('startCleanup POSTs /cleanup-local and surfaces a backend rejection verbatim', async () => {
    const store = useAdminStorageStore()
    apiPost.mockRejectedValueOnce({ status: 400, message: '仍存在未完成迁移的文件，不能清理' })

    const result = await store.startCleanup()

    expect(apiPost).toHaveBeenCalledWith('/admin/storage/cleanup-local')
    expect(result.success).toBe(false)
    expect(result.error).toBe('仍存在未完成迁移的文件，不能清理')
    expect(store.error).toBe('仍存在未完成迁移的文件，不能清理')
  })
})
