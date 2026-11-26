/**
 * 后台「数据库容灾备份」store。
 *
 * 数据源（全部 admin-only）：
 * - GET  /admin/backup/settings          备份开关 / 间隔 / 保留份数 / COS 前缀 / 最近一次备份
 * - PUT  /admin/backup/settings          保存上述设置（只提交前 5 个字段）
 * - POST /admin/backup/run               立即触发一次手动备份（409 = 已有备份执行中）
 * - GET  /admin/backup/runs?page=&page_size=  备份历史（分页）
 * - GET  /admin/backup/runs/{id}/download    下载 gzip 备份（Content-Disposition 带文件名）
 *
 * 约定与其它后台 store 一致：每个 action 都返回 { success, data, error } 且不抛错；
 * 错误文案优先取后端中文 detail（err.detail / err.original.response.data.detail / err.message）。
 * 轮询由页面按 settings.running 驱动，store 不持有定时器。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import api from '@/utils/api'
import { downloadBlobFile, parseContentDispositionFilename } from '@/utils/downloadFile'

const emptySettings = {
  enabled: false,
  interval_minutes: 60,
  retention_count: 48,
  prefix: 'db-backups',
  include_notifications: false,
  cos_configured: false,
  storage_mode: 'LOCAL',
  running: false,
  last_backup: null,
  next_run_at: null,
}

const emptyRun = {
  id: null,
  status: 'RUNNING',
  trigger: 'SCHEDULED',
  object_key: '',
  size_bytes: 0,
  started_at: null,
  finished_at: null,
  duration_ms: null,
  error_message: '',
  include_notifications: false,
}

function toInt(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback
}

function normalizeRun(run) {
  if (!run || typeof run !== 'object') {
    return null
  }
  const source = run
  return {
    ...emptyRun,
    ...source,
    status: source.status === 'SUCCESS' || source.status === 'FAILED' ? source.status : 'RUNNING',
    trigger: source.trigger === 'MANUAL' ? 'MANUAL' : 'SCHEDULED',
    object_key: source.object_key || '',
    size_bytes: toInt(source.size_bytes, 0),
    duration_ms: source.duration_ms == null ? null : toInt(source.duration_ms, 0),
    error_message: source.error_message || '',
    include_notifications: source.include_notifications === true,
  }
}

function normalizeSettings(data) {
  const source = data || {}
  return {
    ...emptySettings,
    ...source,
    enabled: source.enabled === true,
    interval_minutes: toInt(source.interval_minutes, emptySettings.interval_minutes),
    retention_count: toInt(source.retention_count, emptySettings.retention_count),
    prefix: source.prefix || emptySettings.prefix,
    include_notifications: source.include_notifications === true,
    cos_configured: source.cos_configured === true,
    storage_mode: source.storage_mode === 'COS' ? 'COS' : 'LOCAL',
    running: source.running === true,
    last_backup: normalizeRun(source.last_backup),
    next_run_at: source.next_run_at || null,
  }
}

// axios 的 headers key 视运行环境可能大小写不一，统一按小写找
function readContentDisposition(headers) {
  if (!headers) {
    return ''
  }
  const direct = headers['content-disposition']
  if (direct) {
    return direct
  }
  const key = Object.keys(headers).find((name) => name.toLowerCase() === 'content-disposition')
  return key ? headers[key] : ''
}

function fallbackDownloadName(run) {
  const key = String(run?.object_key || '')
  const base = key.split(/[\\/]/).pop()?.trim()
  if (base) return base
  return run?.id ? `database-backup-${run.id}.sql.gz` : 'database-backup.sql.gz'
}

// 下载失败时后端可能返回 JSON，但 responseType=blob 让 data 变成 Blob；
// 尽量把里面的中文 detail 解出来，解不出来就让调用方用通用文案。
async function readBlobDetail(data) {
  if (typeof Blob === 'undefined' || !(data instanceof Blob)) {
    return null
  }
  try {
    const text = await data.text()
    const parsed = JSON.parse(text)
    return typeof parsed?.detail === 'string' && parsed.detail ? parsed.detail : null
  } catch {
    return null
  }
}

export const useAdminBackupStore = defineStore('adminBackup', () => {
  const settings = ref({ ...emptySettings })
  const runs = ref([])
  const page = ref(1)
  const pageSize = ref(10)
  const total = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const requesting = ref(false)
  const error = ref('')

  // 拦截层已把字符串 detail 解成 err.message；这里同时保留原始 detail，
  // 让 409「已有备份执行中」等中文原因能原样展示。
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
      const response = await api.get('/admin/backup/settings')
      settings.value = normalizeSettings(response.data)
      return { success: true, data: settings.value }
    } catch (err) {
      return fail(err, '加载备份设置失败')
    } finally {
      loading.value = false
    }
  }

  async function fetchRuns(targetPage = page.value) {
    const nextPage = Math.max(1, toInt(targetPage, 1))
    loading.value = true
    error.value = ''
    try {
      const response = await api.get('/admin/backup/runs', {
        params: { page: nextPage, page_size: pageSize.value },
      })
      const data = response.data || {}
      runs.value = (Array.isArray(data.items) ? data.items : []).map(normalizeRun).filter(Boolean)
      page.value = Math.max(1, toInt(data.page, nextPage))
      pageSize.value = Math.max(1, toInt(data.page_size, pageSize.value))
      total.value = Math.max(0, toInt(data.total, 0))
      return {
        success: true,
        data: { items: runs.value, total: total.value, page: page.value, page_size: pageSize.value },
      }
    } catch (err) {
      return fail(err, '加载备份记录失败')
    } finally {
      loading.value = false
    }
  }

  // 保存设置和请求备份后都重新拉取 settings + runs，保证状态 / 历史与后端一致。
  async function refresh() {
    await Promise.all([fetchSettings(), fetchRuns(page.value)])
  }

  async function saveSettings(payload) {
    saving.value = true
    error.value = ''
    try {
      const response = await api.put('/admin/backup/settings', payload)
      settings.value = normalizeSettings(response.data)
      await refresh()
      return { success: true, data: settings.value }
    } catch (err) {
      return fail(err, '保存备份设置失败')
    } finally {
      saving.value = false
    }
  }

  async function requestRun() {
    requesting.value = true
    error.value = ''
    try {
      const response = await api.post('/admin/backup/run')
      const data = response.data || {}
      const result = {
        requested: data.requested === true,
        running: data.running === true,
      }
      await refresh()
      return { success: true, data: result }
    } catch (err) {
      return fail(err, '请求备份失败')
    } finally {
      requesting.value = false
    }
  }

  async function downloadRun(run) {
    if (!run?.id) {
      error.value = '缺少备份记录 ID，无法下载'
      return { success: false, error: error.value }
    }
    error.value = ''
    try {
      const response = await api.get(`/admin/backup/runs/${run.id}/download`, {
        responseType: 'blob',
      })
      const payload = response.data
      const blob = typeof Blob !== 'undefined' && payload instanceof Blob
        ? payload
        : new Blob([payload], { type: 'application/gzip' })
      const filename =
        parseContentDispositionFilename(readContentDisposition(response.headers)) ||
        fallbackDownloadName(run)
      downloadBlobFile(blob, filename)
      return { success: true, data: { filename } }
    } catch (err) {
      const detail =
        err?.detail ??
        err?.original?.response?.data?.detail ??
        (await readBlobDetail(err?.original?.response?.data))
      const message = (typeof detail === 'string' && detail) || err?.message || '下载备份失败'
      error.value = message
      return { success: false, error: message, detail: detail ?? null }
    }
  }

  return {
    settings,
    runs,
    page,
    pageSize,
    total,
    loading,
    saving,
    requesting,
    error,
    fetchSettings,
    fetchRuns,
    saveSettings,
    requestRun,
    downloadRun,
  }
})
