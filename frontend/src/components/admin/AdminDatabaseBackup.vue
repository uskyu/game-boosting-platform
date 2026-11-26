<script setup>
/**
 * 后台「数据库容灾备份」卡片。
 *
 * 接口（store: @/stores/adminBackup）：
 * - GET/PUT /admin/backup/settings
 * - POST    /admin/backup/run
 * - GET     /admin/backup/runs?page=&page_size=
 * - GET     /admin/backup/runs/{id}/download
 *
 * 状态区只读展示 COS 配置 / 最近一次备份 / 下次执行；表单可编辑开关、间隔、
 * 保留份数、COS 目录前缀和是否包含通知历史。手动备份请求成功后按 5 秒轮询
 * settings + runs，running 变 false 或组件卸载立即停表，不做永久轮询。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

import CompactPagination from '@/components/CompactPagination.vue'
import { useAdminBackupStore } from '@/stores/adminBackup'
import { formatDateTime } from '@/utils/display'

const store = useAdminBackupStore()

const form = reactive({
  enabled: false,
  interval_minutes: 60,
  retention_count: 48,
  prefix: 'db-backups',
  include_notifications: false,
})

const notice = ref({ type: '', text: '' })
const settingsLoaded = ref(false)
const refreshing = ref(false)
const downloadingId = ref(null)

let pollTimer = null
let pollInFlight = false

const settings = computed(() => store.settings)
const runs = computed(() => store.runs)
const total = computed(() => Number(store.total || 0))
const page = computed(() => Number(store.page || 1))
const pageSize = computed(() => Number(store.pageSize || 10))
// 后端按 page_size=10 返回；这里再截一次，任何情况下都不在页面里渲染超过一页的记录
const visibleRuns = computed(() => runs.value.slice(0, pageSize.value))
const loading = computed(() => store.loading === true)
const saving = computed(() => store.saving === true)
const requesting = computed(() => store.requesting === true)
const cosConfigured = computed(() => settings.value.cos_configured === true)
const running = computed(() => settings.value.running === true)
const pageCount = computed(() => {
  const size = Math.max(1, pageSize.value)
  return Math.max(1, Math.ceil(total.value / size))
})
const lastBackup = computed(() => settings.value.last_backup)
const storageModeLabel = computed(() => (
  settings.value.storage_mode === 'COS' ? '存储方式：腾讯云 COS' : '存储方式：本地服务器'
))
// 按「当前表单间隔 × 保留份数」估算最长可回溯时间，提醒管理员 5 分钟 + 48 份只有 4 小时。
const coverageLabel = computed(() => {
  const interval = Number(form.interval_minutes)
  const retention = Number(form.retention_count)
  if (!Number.isFinite(interval) || !Number.isFinite(retention) || interval <= 0 || retention <= 0) {
    return ''
  }
  const minutes = interval * retention
  if (minutes < 60) return `约 ${Math.round(minutes)} 分钟`
  const hours = minutes / 60
  if (hours < 24) return `约 ${hours < 10 ? hours.toFixed(1) : Math.round(hours)} 小时`
  const days = hours / 24
  return `约 ${days < 10 ? days.toFixed(1) : Math.round(days)} 天`
})

function syncForm() {
  form.enabled = settings.value.enabled === true
  form.interval_minutes = Number(settings.value.interval_minutes || 60)
  form.retention_count = Number(settings.value.retention_count || 48)
  form.prefix = settings.value.prefix || 'db-backups'
  form.include_notifications = settings.value.include_notifications === true
}

function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = bytes
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`
}

function formatDuration(value) {
  if (value === null || value === undefined || value === '') return '—'
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)} 毫秒`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  const minutes = Math.floor(seconds / 60)
  const remaining = Math.round(seconds % 60)
  return `${minutes} 分 ${String(remaining).padStart(2, '0')} 秒`
}

function statusMeta(status) {
  if (status === 'SUCCESS') {
    return { label: '成功', className: '!bg-success-soft !text-success' }
  }
  if (status === 'FAILED') {
    return { label: '失败', className: '!bg-danger-soft !text-danger' }
  }
  return { label: '执行中', className: '!bg-info-soft !text-info' }
}

function triggerLabel(trigger) {
  return trigger === 'MANUAL' ? '手动' : '定时'
}

function validateForm() {
  const interval = Number(form.interval_minutes)
  if (!Number.isInteger(interval) || interval < 5 || interval > 1440) {
    return '执行间隔需为 5–1440 的整数（分钟）'
  }
  const retention = Number(form.retention_count)
  if (!Number.isInteger(retention) || retention < 1 || retention > 500) {
    return '保留份数需为 1–500 的整数'
  }
  if (!form.prefix.trim()) {
    return 'COS 目录前缀不能为空，默认 db-backups'
  }
  return ''
}

function buildSavePayload() {
  return {
    enabled: form.enabled === true,
    interval_minutes: Number(form.interval_minutes),
    retention_count: Number(form.retention_count),
    prefix: form.prefix.trim(),
    include_notifications: form.include_notifications === true,
  }
}

async function save() {
  if (saving.value) return
  if (!settingsLoaded.value) {
    notice.value = { type: 'error', text: '备份设置尚未加载成功，请刷新后重试' }
    return
  }
  const validationError = validateForm()
  if (validationError) {
    notice.value = { type: 'error', text: validationError }
    return
  }
  notice.value = { type: '', text: '' }
  const result = await store.saveSettings(buildSavePayload())
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '保存备份设置失败' }
    return
  }
  settingsLoaded.value = true
  syncForm()
  syncPolling()
  notice.value = { type: 'success', text: '备份设置已保存' }
}

// ── 手动备份 / 5 秒轮询 ──

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    if (pollInFlight) return
    pollInFlight = true
    try {
      // 轮询不调 syncForm，避免覆盖管理员正在编辑但还没保存的表单
      const settingsResult = await store.fetchSettings()
      await store.fetchRuns(page.value)
      if (!settingsResult.success) stopPolling()
      else if (!store.settings.running) stopPolling()
    } finally {
      pollInFlight = false
    }
  }, 5000)
}

function syncPolling() {
  if (running.value) startPolling()
  else stopPolling()
}

async function requestBackup() {
  if (requesting.value || running.value || !cosConfigured.value) return
  notice.value = { type: '', text: '' }
  const result = await store.requestRun()
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '请求备份失败' }
    // 409「已有备份执行中」时后端状态已变成 running：补拉一次并进入轮询，
    // 让按钮和状态区与真实进度同步。
    await store.fetchSettings()
    syncPolling()
    return
  }
  notice.value = { type: 'success', text: '备份请求已提交，页面每 5 秒自动刷新进度。' }
  // 手动请求后无条件起表；首轮发现已跑完（running=false）会自行停表。
  startPolling()
}

async function refreshAll() {
  if (refreshing.value) return
  refreshing.value = true
  notice.value = { type: '', text: '' }
  const [settingsResult, runsResult] = await Promise.all([
    store.fetchSettings(),
    store.fetchRuns(page.value),
  ])
  refreshing.value = false
  if (!settingsResult.success) {
    notice.value = { type: 'error', text: settingsResult.error || '加载备份设置失败' }
  } else if (!runsResult.success) {
    notice.value = { type: 'error', text: runsResult.error || '加载备份记录失败' }
  } else {
    syncForm()
  }
  syncPolling()
}

async function loadPage(target) {
  const result = await store.fetchRuns(target)
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '加载备份记录失败' }
  }
}

async function download(run) {
  if (downloadingId.value) return
  downloadingId.value = run.id
  const result = await store.downloadRun(run)
  downloadingId.value = null
  notice.value = result.success
    ? { type: 'success', text: '备份文件已开始下载' }
    : { type: 'error', text: result.error || '下载备份失败' }
}

onMounted(async () => {
  const [settingsResult, runsResult] = await Promise.all([
    store.fetchSettings(),
    store.fetchRuns(1),
  ])
  if (!settingsResult.success) {
    notice.value = { type: 'error', text: settingsResult.error || '加载备份设置失败' }
  } else {
    settingsLoaded.value = true
    syncForm()
  }
  if (!runsResult.success) {
    notice.value = { type: 'error', text: runsResult.error || '加载备份记录失败' }
  }
  // 进页面时若已有备份在跑（例如另一位管理员触发），同样按 5 秒跟进
  syncPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <section class="rounded-tile border border-line-1 p-4 sm:p-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <h3 class="text-xl font-semibold text-ink-1">数据库容灾备份</h3>
        <p class="mt-2 text-sm leading-6 text-ink-2">
          把数据库定期备份到腾讯云 COS 的独立目录；备份包含用户 / 订单 / 钱包 / 聊天等业务数据，
          默认不含通知历史（通知是临时数据且占库最大）。备份固定写入 COS（与图片当前存储方式无关），
          不含 COS 里的图片对象。
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span v-if="running" class="tag !bg-info-soft !text-info">备份执行中</span>
        <button
          type="button"
          class="btn-primary min-h-[40px] !px-4 !py-2"
          :disabled="requesting || running || !cosConfigured || !settingsLoaded"
          @click="requestBackup"
        >
          {{ requesting ? '请求中...' : running ? '备份执行中...' : '立即备份' }}
        </button>
        <button
          type="button"
          class="btn-ghost min-h-[40px] !px-4 !py-2"
          :disabled="refreshing"
          @click="refreshAll"
        >
          {{ refreshing ? '刷新中...' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">
      {{ notice.text }}
    </div>

    <!-- 顶部状态区：COS 配置 / 最近一次备份 / 下次执行 -->
    <div class="mt-5 grid gap-3 sm:grid-cols-3">
      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-xs text-ink-3">COS 状态</p>
        <p class="mt-2 flex flex-wrap items-center gap-2">
          <span class="tag" :class="cosConfigured ? '!bg-success-soft !text-success' : '!bg-danger-soft !text-danger'">
            {{ cosConfigured ? '已配置' : '未配置' }}
          </span>
          <span class="text-xs text-ink-3">{{ storageModeLabel }}</span>
        </p>
        <p v-if="!cosConfigured" class="mt-2 text-xs leading-5 text-danger">
          请先在对象存储中配置腾讯云 COS 后再启用备份
        </p>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-xs text-ink-3">最近一次备份</p>
        <template v-if="lastBackup">
          <p class="mt-2 flex flex-wrap items-center gap-2">
            <span class="tag" :class="statusMeta(lastBackup.status).className">{{ statusMeta(lastBackup.status).label }}</span>
            <span class="text-xs tabular-nums text-ink-3">{{ formatDateTime(lastBackup.started_at) }}</span>
          </p>
          <p class="mt-1 text-xs leading-5 tabular-nums text-ink-2">
            {{ formatBytes(lastBackup.size_bytes) }} · 耗时 {{ formatDuration(lastBackup.duration_ms) }} · {{ triggerLabel(lastBackup.trigger) }}
          </p>
          <p v-if="lastBackup.status === 'FAILED' && lastBackup.error_message" class="mt-1 break-words text-xs leading-5 text-danger">
            {{ lastBackup.error_message }}
          </p>
        </template>
        <p v-else class="mt-2 text-sm text-ink-3">暂无备份</p>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-xs text-ink-3">下次执行</p>
        <p class="mt-2 text-sm tabular-nums text-ink-1">
          {{ settings.enabled && settings.next_run_at ? formatDateTime(settings.next_run_at) : '未启用自动备份' }}
        </p>
        <p v-if="running" class="mt-1 text-xs leading-5 text-info">备份正在执行，完成后会重新计算下次执行时间。</p>
      </div>
    </div>

    <!-- 备份设置 -->
    <form class="mt-5 grid gap-4 sm:grid-cols-2" @submit.prevent="save">
      <div class="rounded-tile border border-line-1 p-4 sm:col-span-2">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">启用自动备份</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">按下方间隔定时把数据库导出并上传到 COS 独立目录。</p>
          </div>
          <button
            type="button"
            role="switch"
            :aria-checked="form.enabled"
            :disabled="!cosConfigured || !settingsLoaded"
            :class="[
              form.enabled ? 'filter-pill-active' : 'filter-pill',
              (!cosConfigured || !settingsLoaded) ? 'cursor-not-allowed opacity-60' : '',
            ]"
            @click="form.enabled = !form.enabled"
          >
            {{ form.enabled ? '已开启' : '已关闭' }}
          </button>
        </div>
      </div>

      <div>
        <label class="label" for="backup-interval-minutes">执行间隔（分钟）</label>
        <input
          id="backup-interval-minutes"
          v-model.number="form.interval_minutes"
          type="number"
          min="5"
          max="1440"
          step="1"
          required
          class="input min-h-[44px]"
        />
        <p class="helper-text">5–1440 分钟，默认 60 分钟。</p>
      </div>

      <div>
        <label class="label" for="backup-retention-count">保留份数</label>
        <input
          id="backup-retention-count"
          v-model.number="form.retention_count"
          type="number"
          min="1"
          max="500"
          step="1"
          required
          class="input min-h-[44px]"
        />
        <p class="helper-text">1–500 份，默认 48 份；超出后自动删除最旧的备份。</p>
        <p v-if="coverageLabel" class="mt-1 text-xs leading-5 text-ink-3">
          按当前间隔与保留份数，最多可回溯 {{ coverageLabel }}；实际以成功备份为准。
        </p>
      </div>

      <div class="sm:col-span-2">
        <label class="label" for="backup-prefix">COS 目录前缀</label>
        <input
          id="backup-prefix"
          v-model="form.prefix"
          type="text"
          maxlength="100"
          class="input min-h-[44px]"
          placeholder="db-backups"
        />
        <p class="helper-text">
          默认 db-backups。多站共用同一存储桶时请分别设置，例如 db-backups/cn1、db-backups/cn2，避免互相覆盖。
        </p>
      </div>

      <label class="flex items-start gap-3 rounded-tile border border-line-1 p-4 sm:col-span-2">
        <input v-model="form.include_notifications" type="checkbox" class="mt-0.5 h-4 w-4 accent-primary" />
        <span class="min-w-0">
          <span class="block text-sm font-medium text-ink-1">包含通知历史</span>
          <span class="mt-1 block text-xs leading-5 text-ink-3">默认不包含。勾选后备份全量更大、耗时更慢。</span>
        </span>
      </label>

      <div class="flex flex-wrap items-center gap-3 sm:col-span-2">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="saving || !settingsLoaded">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
        <p class="text-xs leading-5 text-ink-3">保存后立即生效；关闭自动备份不会删除已有备份文件。</p>
      </div>
    </form>

    <!-- 恢复说明：灾备的最后一步是能恢复，而不只是有文件 -->
    <details class="mt-5 rounded-tile border border-line-1 p-4">
      <summary class="cursor-pointer text-sm font-semibold text-ink-1">恢复备份（谨慎操作）</summary>
      <div class="mt-2 space-y-1 text-xs leading-5 text-ink-3">
        <p>1. 在下方记录中下载最近的 .sql.gz 文件并解压得到 .sql。</p>
        <p>2. 建议先停止后端写入，并对当前数据库做一次快照；恢复会覆盖同名表。</p>
        <p>3. 在目标 MySQL 导入：<code>mysql -h &lt;主机&gt; -u &lt;用户&gt; &lt;库名&gt; &lt; backup.sql</code></p>
        <p>4. 启动后端；若备份版本落后，启动入口会自动执行 alembic upgrade head。</p>
        <p>5. 验证登录、订单、钱包与聊天数据。默认备份不含通知历史数据，恢复后通知表为空。</p>
        <p>6. 若换到新服务器且 SECRET_KEY 与备份时不同，后台加密保存的 COS/支付等密钥需要重新填写。</p>
      </div>
    </details>

    <p class="mt-3 text-xs leading-5 text-ink-3">
      备份包含账号密码哈希等敏感数据，COS 存储桶必须保持私有；下载仅限管理员，请勿外传。
    </p>

    <!-- 备份历史 -->
    <div class="mt-6 border-t border-line-1 pt-5">
      <div class="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p class="text-sm font-semibold text-ink-1">备份记录</p>
          <p class="mt-1 text-xs leading-5 text-ink-3">每页最多 10 条；成功的备份可直接下载。</p>
        </div>
        <p v-if="total > 0" class="text-xs tabular-nums text-ink-3">共 {{ total }} 条</p>
      </div>

      <div class="mt-3 overflow-x-auto">
        <table class="w-full min-w-[880px] text-left text-sm">
          <thead>
            <tr class="border-b border-line text-xs text-ink-3">
              <th class="whitespace-nowrap px-2 py-3 font-medium">时间</th>
              <th class="whitespace-nowrap px-2 py-3 font-medium">触发方式</th>
              <th class="px-2 py-3 font-medium">状态</th>
              <th class="whitespace-nowrap px-2 py-3 font-medium">大小</th>
              <th class="whitespace-nowrap px-2 py-3 font-medium">耗时</th>
              <th class="px-2 py-3 font-medium">对象路径</th>
              <th class="whitespace-nowrap px-2 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody v-if="visibleRuns.length">
            <tr v-for="run in visibleRuns" :key="run.id" class="border-b border-line/70 align-top">
              <td class="whitespace-nowrap px-2 py-3 tabular-nums text-ink-2">{{ formatDateTime(run.started_at) }}</td>
              <td class="whitespace-nowrap px-2 py-3 text-ink-2">{{ triggerLabel(run.trigger) }}</td>
              <td class="px-2 py-3">
                <span class="tag" :class="statusMeta(run.status).className">{{ statusMeta(run.status).label }}</span>
                <p
                  v-if="run.status === 'FAILED' && run.error_message"
                  class="mt-1 max-w-[240px] whitespace-normal break-words text-xs leading-5 text-danger"
                >
                  {{ run.error_message }}
                </p>
              </td>
              <td class="whitespace-nowrap px-2 py-3 tabular-nums text-ink-2">{{ formatBytes(run.size_bytes) }}</td>
              <td class="whitespace-nowrap px-2 py-3 tabular-nums text-ink-2">{{ formatDuration(run.duration_ms) }}</td>
              <td class="px-2 py-3">
                <p class="max-w-[260px] truncate font-mono text-xs text-ink-2" :title="run.object_key">
                  {{ run.object_key || '—' }}
                </p>
              </td>
              <td class="whitespace-nowrap px-2 py-3">
                <button
                  v-if="run.status === 'SUCCESS'"
                  type="button"
                  class="btn-secondary min-h-[36px] !px-3 !py-1.5"
                  :disabled="downloadingId === run.id"
                  @click="download(run)"
                >
                  {{ downloadingId === run.id ? '下载中...' : '下载' }}
                </button>
                <span v-else class="text-xs text-ink-3">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="loading && !visibleRuns.length" class="py-8 text-center text-sm text-ink-3">加载中…</p>
        <p v-else-if="!visibleRuns.length" class="py-10 text-center text-sm text-ink-3">暂无备份记录</p>
      </div>

      <CompactPagination
        :page="page"
        :page-count="pageCount"
        :total="total"
        @change="loadPage"
      />
    </div>
  </section>
</template>
