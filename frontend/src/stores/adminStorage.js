/**
 * 后台「对象存储」store。
 *
 * 数据源（全部 admin-only）：
 * - GET/PUT /admin/storage/settings        存储方式与腾讯云 COS 配置（密钥打码回显）
 * - POST    /admin/storage/test            用已保存或临时填写的配置做一次连通性测试
 * - POST    /admin/storage/migrate         启动/继续历史图片迁移（可暂停，进度落库不丢）
 * - POST    /admin/storage/migrate/pause   暂停迁移
 * - GET     /admin/storage/migrate/status  迁移进度 total/done/failed/pending/running...
 * - POST    /admin/storage/cleanup-local   删除已迁移完成的本地图片
 * - GET     /admin/storage/cleanup/status  清理进度 running/deleted/failed/remaining
 *
 * 约定与其它后台 store 一致：每个 action 都返回 { success, data, error } 且不抛错；
 * 后端 400 的中文 detail 由 api 拦截层转成 err.message，这里额外把原始 detail
 * 一起透出（结构化 detail 时调用方可进一步判断）。密钥字段不落任何前端日志。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import api from '@/utils/api'

// 与后端保持一致：提交该字面量表示「保留数据库里已保存的密钥」
export const KEEP_SECRET_PLACEHOLDER = '__KEEP__'

const emptySettings = {
  storage_mode: 'LOCAL',
  cos_bucket: '',
  cos_region: '',
  cos_cdn_domain: '',
  cos_secret_id_masked: '',
  cos_secret_key_masked: '',
  cos_configured: false,
}

const emptyMigrationStatus = {
  total: 0,
  done: 0,
  failed: 0,
  pending: 0,
  running: false,
  paused: false,
  current_path: '',
  last_error: '',
  local_files: 0,
  local_bytes: 0,
}

const emptyCleanupStatus = {
  running: false,
  deleted: 0,
  failed: 0,
  remaining: 0,
  last_error: '',
}

function toInt(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.trunc(parsed) : 0
}

function normalizeSettings(data) {
  const source = data || {}
  return {
    ...emptySettings,
    ...source,
    storage_mode: source.storage_mode === 'COS' ? 'COS' : 'LOCAL',
    cos_bucket: source.cos_bucket || '',
    cos_region: source.cos_region || '',
    cos_cdn_domain: source.cos_cdn_domain || '',
    cos_secret_id_masked: source.cos_secret_id_masked || '',
    cos_secret_key_masked: source.cos_secret_key_masked || '',
    cos_configured: source.cos_configured === true,
  }
}

function normalizeMigrationStatus(data) {
  const source = data || {}
  return {
    ...emptyMigrationStatus,
    ...source,
    total: toInt(source.total),
    done: toInt(source.done),
    failed: toInt(source.failed),
    pending: toInt(source.pending),
    running: source.running === true,
    paused: source.paused === true,
    current_path: source.current_path || '',
    last_error: source.last_error || '',
    local_files: toInt(source.local_files),
    local_bytes: toInt(source.local_bytes),
  }
}

function normalizeCleanupStatus(data) {
  const source = data || {}
  return {
    ...emptyCleanupStatus,
    ...source,
    running: source.running === true,
    deleted: toInt(source.deleted),
    failed: toInt(source.failed),
    remaining: toInt(source.remaining),
    last_error: source.last_error || '',
  }
}

export const useAdminStorageStore = defineStore('adminStorage', () => {
  const settings = ref({ ...emptySettings })
  const migrationStatus = ref({ ...emptyMigrationStatus })
  const cleanupStatus = ref({ ...emptyCleanupStatus })
  const loading = ref(false)
  const error = ref('')

  // 拦截层已把字符串 detail 解成 err.message；这里同时保留原始 detail，
  // 让 400 的中文原因能原样展示，结构化 detail 也不丢。
  function fail(err, fallback = '操作失败') {
    const detail = err?.detail ?? err?.original?.response?.data?.detail
    const message = (typeof detail === 'string' && detail) || err?.message || fallback
    error.value = message
    return { success: false, error: message, detail: detail ?? null }
  }

  async function fetchSettings() {
    loading.value = true
    error.value = ''
    try {
      const response = await api.get('/admin/storage/settings')
      settings.value = normalizeSettings(response.data)
      return { success: true, data: settings.value }
    } catch (err) {
      return fail(err, '加载存储设置失败')
    } finally {
      loading.value = false
    }
  }

  async function saveSettings(payload) {
    loading.value = true
    error.value = ''
    try {
      const response = await api.put('/admin/storage/settings', payload)
      settings.value = normalizeSettings(response.data)
      return { success: true, data: settings.value }
    } catch (err) {
      return fail(err, '保存存储设置失败')
    } finally {
      loading.value = false
    }
  }

  // 测试连接不写 settings（这是探测，不是保存）；ok=false 时 message 仍为后端中文说明。
  async function testConnection(payload = {}) {
    error.value = ''
    try {
      const response = await api.post('/admin/storage/test', payload)
      const data = response.data || {}
      return { success: true, data: { ok: data.ok === true, message: data.message || '' } }
    } catch (err) {
      return fail(err, '测试连接失败')
    }
  }

  async function fetchMigrationStatus() {
    error.value = ''
    try {
      const response = await api.get('/admin/storage/migrate/status')
      migrationStatus.value = normalizeMigrationStatus(response.data)
      return { success: true, data: migrationStatus.value }
    } catch (err) {
      return fail(err, '加载迁移进度失败')
    }
  }

  // 启动/继续迁移：POST 可能只是唤醒后台任务，进度以 status 为准，立即补拉一次。
  async function startMigration() {
    error.value = ''
    try {
      await api.post('/admin/storage/migrate')
      await fetchMigrationStatus()
      return { success: true, data: migrationStatus.value }
    } catch (err) {
      return fail(err, '启动迁移失败')
    }
  }

  async function pauseMigration() {
    error.value = ''
    try {
      await api.post('/admin/storage/migrate/pause')
      await fetchMigrationStatus()
      return { success: true, data: migrationStatus.value }
    } catch (err) {
      return fail(err, '暂停迁移失败')
    }
  }

  async function fetchCleanupStatus() {
    error.value = ''
    try {
      const response = await api.get('/admin/storage/cleanup/status')
      cleanupStatus.value = normalizeCleanupStatus(response.data)
      return { success: true, data: cleanupStatus.value }
    } catch (err) {
      return fail(err, '加载清理进度失败')
    }
  }

  async function startCleanup() {
    error.value = ''
    try {
      await api.post('/admin/storage/cleanup-local')
      await fetchCleanupStatus()
      return { success: true, data: cleanupStatus.value }
    } catch (err) {
      return fail(err, '启动清理失败')
    }
  }

  return {
    settings,
    migrationStatus,
    cleanupStatus,
    loading,
    error,
    fetchSettings,
    saveSettings,
    testConnection,
    startMigration,
    pauseMigration,
    fetchMigrationStatus,
    startCleanup,
    fetchCleanupStatus,
  }
})
