<script setup>
/**
 * 后台「对象存储」设置。
 *
 * 接口（store: @/stores/adminStorage）：
 * - GET/PUT /admin/storage/settings
 * - POST    /admin/storage/test
 * - POST    /admin/storage/migrate、POST /admin/storage/migrate/pause
 * - GET     /admin/storage/migrate/status
 * - POST    /admin/storage/cleanup-local、GET /admin/storage/cleanup/status
 *
 * 三块相互关联但各自独立的状态：
 * 1. 存储方式 + 腾讯云 COS 配置：保存后新图片写入 COS，历史图片仍可读取；
 *    密钥只回显打码尾号，不填写时提交 "__KEEP__" 保留原值。
 * 2. 历史图片迁移：可暂停，进度落库（刷新/重启不丢），失败行下一轮重试；
 *    本地文件不会自动删除，必须最后单独点清理。
 * 3. 清理本地文件：仅在迁移全部成功（failed/pending 均为 0）时可用，
 *    且必须二次确认——删除后本地不再保留副本。
 *
 * 轮询策略：只在 running=true 时按 2 秒拉进度，running 变 false 或组件卸载立即清掉，
 * 不做永久轮询。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { KEEP_SECRET_PLACEHOLDER, useAdminStorageStore } from '@/stores/adminStorage'

const store = useAdminStorageStore()

const form = reactive({
  storage_mode: 'LOCAL',
  cos_bucket: '',
  cos_region: '',
  cos_cdn_domain: '',
})
// 密钥输入框默认留空：只有管理员重新填写时才覆盖，留空一律提交 __KEEP__
const secretIdInput = ref('')
const secretKeyInput = ref('')

const notice = ref({ type: '', text: '' })
const testNotice = ref({ type: '', text: '' })
const testing = ref(false)
const saving = ref(false)
const migrationBusy = ref(false)
const cleanupBusy = ref(false)
const settingsLoaded = ref(false)

const settings = computed(() => store.settings)
const migrationStatus = computed(() => store.migrationStatus)
const cleanupStatus = computed(() => store.cleanupStatus)

const cosMuted = computed(() => form.storage_mode === 'LOCAL')

function syncForm() {
  form.storage_mode = settings.value.storage_mode === 'COS' ? 'COS' : 'LOCAL'
  form.cos_bucket = settings.value.cos_bucket || ''
  form.cos_region = settings.value.cos_region || ''
  form.cos_cdn_domain = settings.value.cos_cdn_domain || ''
  // 打码值只做 placeholder，输入框本体始终清空，避免把掩码当真实密钥提交
  secretIdInput.value = ''
  secretKeyInput.value = ''
}

const secretIdPlaceholder = computed(() =>
  settings.value.cos_secret_id_masked
    ? `已配置：${settings.value.cos_secret_id_masked}（留空则保留）`
    : '请输入腾讯云 SecretId'
)
const secretKeyPlaceholder = computed(() =>
  settings.value.cos_secret_key_masked
    ? `已配置：${settings.value.cos_secret_key_masked}（留空则保留）`
    : '请输入腾讯云 SecretKey'
)

const migrationTotal = computed(() => Number(migrationStatus.value.total || 0))
const migrationDone = computed(() => Number(migrationStatus.value.done || 0))
const migrationFailed = computed(() => Number(migrationStatus.value.failed || 0))
const migrationPending = computed(() => Number(migrationStatus.value.pending || 0))
const migrationRunning = computed(() => migrationStatus.value.running === true)
const migrationProgress = computed(() => {
  if (migrationTotal.value <= 0) return 0
  const percent = (migrationDone.value / migrationTotal.value) * 100
  return Math.min(100, Math.max(0, Math.round(percent)))
})
const migrationCanResume = computed(() => migrationStatus.value.paused === true || migrationFailed.value > 0)

const cleanupRunning = computed(() => cleanupStatus.value.running === true)
const cleanupDeleted = computed(() => Number(cleanupStatus.value.deleted || 0))
const cleanupFailed = computed(() => Number(cleanupStatus.value.failed || 0))
const cleanupRemaining = computed(() => Number(cleanupStatus.value.remaining || 0))
// 只有迁移全部成功（没有失败/待处理）才允许清理本地副本
const canCleanup = computed(() =>
  migrationTotal.value > 0
  && migrationDone.value > 0
  && migrationFailed.value === 0
  && migrationPending.value === 0
  && !migrationRunning.value
  && !cleanupRunning.value
)

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

function buildSavePayload() {
  return {
    storage_mode: form.storage_mode,
    cos_bucket: form.cos_bucket.trim(),
    cos_region: form.cos_region.trim(),
    cos_cdn_domain: form.cos_cdn_domain.trim(),
    // 没重新填写就原样保留数据库里的加密值
    cos_secret_id: secretIdInput.value.trim() || KEEP_SECRET_PLACEHOLDER,
    cos_secret_key: secretKeyInput.value.trim() || KEEP_SECRET_PLACEHOLDER,
  }
}

function buildTestPayload() {
  const payload = {}
  // 只带管理员刚填写的临时值；留空/不传表示用已保存配置测
  if (secretIdInput.value.trim()) payload.cos_secret_id = secretIdInput.value.trim()
  if (secretKeyInput.value.trim()) payload.cos_secret_key = secretKeyInput.value.trim()
  if (form.cos_bucket.trim()) payload.cos_bucket = form.cos_bucket.trim()
  if (form.cos_region.trim()) payload.cos_region = form.cos_region.trim()
  return payload
}

async function save() {
  if (saving.value) return
  if (!settingsLoaded.value) {
    notice.value = { type: 'error', text: '存储设置尚未加载成功，请刷新后重试' }
    return
  }
  saving.value = true
  notice.value = { type: '', text: '' }
  const result = await store.saveSettings(buildSavePayload())
  saving.value = false
  if (!result.success) {
    // 切 COS 凭据无效时后端 400 + 中文 detail，且不会落库：原样展示，不能报成功
    notice.value = { type: 'error', text: result.error || '保存失败' }
    return
  }
  notice.value = { type: 'success', text: '站点/存储设置已保存' }
  settingsLoaded.value = true
  syncForm()
}

async function testConnection() {
  if (testing.value) return
  testing.value = true
  testNotice.value = { type: '', text: '' }
  const result = await store.testConnection(buildTestPayload())
  testing.value = false
  const ok = result.success && result.data?.ok === true
  testNotice.value = {
    type: ok ? 'success' : 'error',
    text: result.success ? (result.data?.message || (ok ? '连接成功' : '连接失败')) : (result.error || '测试连接失败'),
  }
}

// ── 迁移 / 清理轮询：只跟着 running 走 ──

let migrationTimer = null
let cleanupTimer = null
// 启动瞬间后端可能还没把 running 置真（任务在后台线程里拉起）：
// 只补一次延迟状态检查来对齐布尔值，不构成额外轮询。
let migrationKickTimer = null
let cleanupKickTimer = null

function stopMigrationPolling() {
  if (migrationTimer) {
    clearInterval(migrationTimer)
    migrationTimer = null
  }
}

function startMigrationPolling() {
  if (migrationTimer) return
  migrationTimer = setInterval(async () => {
    const result = await store.fetchMigrationStatus()
    if (!result.success || !store.migrationStatus.running) stopMigrationPolling()
  }, 2000)
}

function syncMigrationPolling() {
  if (store.migrationStatus.running) startMigrationPolling()
  else stopMigrationPolling()
}

// 启动成功后若状态仍是 running=false，1.5 秒后再确认一次，避免任务刚拉起就漏掉轮询
function kickMigrationStatusCheck() {
  if (store.migrationStatus.running) return
  if (migrationKickTimer) return
  migrationKickTimer = setTimeout(async () => {
    migrationKickTimer = null
    await store.fetchMigrationStatus()
    syncMigrationPolling()
  }, 1500)
}

function stopCleanupPolling() {
  if (cleanupTimer) {
    clearInterval(cleanupTimer)
    cleanupTimer = null
  }
}

function startCleanupPolling() {
  if (cleanupTimer) return
  cleanupTimer = setInterval(async () => {
    const result = await store.fetchCleanupStatus()
    if (!result.success || !store.cleanupStatus.running) stopCleanupPolling()
  }, 2000)
}

function syncCleanupPolling() {
  if (store.cleanupStatus.running) startCleanupPolling()
  else stopCleanupPolling()
}

function kickCleanupStatusCheck() {
  if (store.cleanupStatus.running) return
  if (cleanupKickTimer) return
  cleanupKickTimer = setTimeout(async () => {
    cleanupKickTimer = null
    await store.fetchCleanupStatus()
    syncCleanupPolling()
  }, 1500)
}

async function startMigration() {
  if (migrationBusy.value) return
  migrationBusy.value = true
  const result = await store.startMigration()
  migrationBusy.value = false
  notice.value = result.success
    ? { type: 'success', text: '迁移任务已启动，可随时暂停；刷新页面或重启服务不会丢进度' }
    : { type: 'error', text: result.error || '启动迁移失败' }
  syncMigrationPolling()
  if (result.success) kickMigrationStatusCheck()
}

async function pauseMigration() {
  if (migrationBusy.value) return
  migrationBusy.value = true
  const result = await store.pauseMigration()
  migrationBusy.value = false
  notice.value = result.success
    ? { type: 'success', text: '迁移已暂停，下次点「继续迁移」从断点接着跑' }
    : { type: 'error', text: result.error || '暂停迁移失败' }
  syncMigrationPolling()
}

async function startCleanup() {
  if (cleanupBusy.value || !canCleanup.value) return
  const confirmed = window.confirm(
    '将删除服务器上所有已迁移成功的本地图片文件。'
    + '删除后本地不再保留副本，仅 COS 有；此后若要回退到本地存储，历史图片将无法从服务器读取。\n\n'
    + `已迁移 ${migrationDone.value} 个文件（共 ${migrationTotal.value} 个）。确认清理？`,
  )
  if (!confirmed) return
  cleanupBusy.value = true
  const result = await store.startCleanup()
  cleanupBusy.value = false
  notice.value = result.success
    ? { type: 'success', text: '本地文件清理已启动' }
    : { type: 'error', text: result.error || '启动清理失败' }
  syncCleanupPolling()
  if (result.success) kickCleanupStatusCheck()
}

async function refreshStatuses() {
  await Promise.all([store.fetchMigrationStatus(), store.fetchCleanupStatus()])
  syncMigrationPolling()
  syncCleanupPolling()
}

onMounted(async () => {
  const result = await store.fetchSettings()
  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '加载存储设置失败，请刷新后重试' }
  } else {
    settingsLoaded.value = true
    syncForm()
  }
  await refreshStatuses()
})

onUnmounted(() => {
  stopMigrationPolling()
  stopCleanupPolling()
  // 补检定时器同样要清掉，否则卸载后仍会发一次状态请求
  if (migrationKickTimer) {
    clearTimeout(migrationKickTimer)
    migrationKickTimer = null
  }
  if (cleanupKickTimer) {
    clearTimeout(cleanupKickTimer)
    cleanupKickTimer = null
  }
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">对象存储</h2>
    <p class="mt-2 text-sm text-ink-2">把新上传的图片存到腾讯云 COS，并迁移服务器上的历史图片。</p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">
      {{ notice.text }}
    </div>

    <form class="mt-6 grid max-w-2xl gap-5" @submit.prevent="save">
      <!-- A. 存储方式总开关 -->
      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-sm font-semibold text-ink-1">存储方式</p>
        <p class="mt-1 text-xs leading-5 text-ink-3">
          关闭时与旧版完全一致，图片直接存服务器；开启后新图写入 COS，历史图仍可正常读取。
        </p>
        <div class="mt-4 grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            :class="form.storage_mode === 'LOCAL' ? 'filter-pill-active' : 'filter-pill'"
            :aria-pressed="form.storage_mode === 'LOCAL'"
            @click="form.storage_mode = 'LOCAL'"
          >
            本地服务器存储
          </button>
          <button
            type="button"
            :class="form.storage_mode === 'COS' ? 'filter-pill-active' : 'filter-pill'"
            :aria-pressed="form.storage_mode === 'COS'"
            @click="form.storage_mode = 'COS'"
          >
            腾讯云对象存储
          </button>
        </div>
        <p v-if="!settings.cos_configured" class="mt-3 text-xs leading-5 text-ink-3">
          尚未保存过 COS 凭据；切到 COS 前建议先填好下面的配置并点「测试连接」。
        </p>
      </div>

      <!-- B. 腾讯云 COS 配置：LOCAL 时仍可编辑（便于先测试再切换），只做视觉弱化 -->
      <div
        class="rounded-tile border border-line-1 p-4 transition-opacity duration-base"
        :class="cosMuted ? 'opacity-60' : ''"
        :aria-disabled="cosMuted"
      >
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">腾讯云 COS 配置</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">
              当前使用本地存储，下面配置仍可填写和测试；保存后切换存储方式才真正生效。
            </p>
          </div>
          <button
            type="button"
            class="btn-secondary min-h-[40px] !px-4 !py-2"
            :disabled="testing"
            @click="testConnection"
          >
            {{ testing ? '测试中...' : '测试连接' }}
          </button>
        </div>

        <div v-if="testNotice.text" class="mt-3" :class="testNotice.type === 'success' ? 'message-success' : 'message-error'">
          {{ testNotice.text }}
        </div>

        <div class="mt-4 space-y-4">
          <div>
            <label class="label" for="cos-secret-id">SecretId</label>
            <input
              id="cos-secret-id"
              v-model="secretIdInput"
              type="password"
              autocomplete="new-password"
              class="input min-h-[44px]"
              :placeholder="secretIdPlaceholder"
            />
          </div>

          <div>
            <label class="label" for="cos-secret-key">SecretKey</label>
            <input
              id="cos-secret-key"
              v-model="secretKeyInput"
              type="password"
              autocomplete="new-password"
              class="input min-h-[44px]"
              :placeholder="secretKeyPlaceholder"
            />
          </div>

          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="label" for="cos-bucket">Bucket</label>
              <input
                id="cos-bucket"
                v-model="form.cos_bucket"
                type="text"
                maxlength="100"
                class="input min-h-[44px]"
                placeholder="例如：game-1314124236"
              />
            </div>
            <div>
              <label class="label" for="cos-region">Region</label>
              <input
                id="cos-region"
                v-model="form.cos_region"
                type="text"
                maxlength="64"
                class="input min-h-[44px]"
                placeholder="例如：ap-chongqing"
              />
            </div>
          </div>

          <div>
            <label class="label" for="cos-cdn-domain">CDN 域名（可选）</label>
            <input
              id="cos-cdn-domain"
              v-model="form.cos_cdn_domain"
              type="text"
              maxlength="255"
              class="input min-h-[44px]"
              placeholder="例如：https://cdn.example.com"
            />
            <p class="helper-text">配置后图片外链优先走 CDN 域名，留空则使用 COS 默认访问域名。</p>
          </div>
        </div>
      </div>

      <!-- C. 保存 -->
      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="saving || !settingsLoaded">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
        <p class="text-xs leading-5 text-ink-3">切换到 COS 时凭据无效会被后端拒绝，且不会保存这一次修改。</p>
      </div>
    </form>

    <!-- D. 历史图片迁移 -->
    <div class="mt-8 border-t border-line-1 pt-6">
      <div class="max-w-2xl rounded-tile border border-line-1 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">历史图片迁移</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">
              开启 COS 后可把服务器上的历史图片批量上传到 COS；任务可在中途暂停，刷新页面/重启不会丢进度；
              <strong class="text-ink-2">本地文件不会自动删除</strong>，需要最后单独点清理。
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button
              type="button"
              class="btn-primary min-h-[40px] !px-4 !py-2"
              :disabled="migrationBusy || migrationRunning"
              @click="startMigration"
            >
              {{ migrationCanResume ? '继续迁移' : '开始迁移' }}
            </button>
            <button
              type="button"
              class="btn-secondary min-h-[40px] !px-4 !py-2"
              :disabled="!migrationRunning || migrationBusy"
              @click="pauseMigration"
            >
              暂停
            </button>
            <button
              type="button"
              class="btn-ghost min-h-[40px] !px-4 !py-2"
              :disabled="migrationBusy"
              @click="refreshStatuses"
            >
              刷新进度
            </button>
          </div>
        </div>

        <!-- 进度：total / done / failed / pending + 简单进度条 -->
        <div class="mt-4 grid gap-3 text-xs leading-5 text-ink-3 sm:grid-cols-4">
          <p>总数 <span class="tabular-nums text-ink-1">{{ migrationTotal }}</span></p>
          <p>已完成 <span class="tabular-nums text-success">{{ migrationDone }}</span></p>
          <p>失败 <span class="tabular-nums" :class="migrationFailed > 0 ? 'text-danger' : 'text-ink-1'">{{ migrationFailed }}</span></p>
          <p>待迁移 <span class="tabular-nums text-ink-1">{{ migrationPending }}</span></p>
        </div>

        <div class="mt-3 h-2 w-full overflow-hidden rounded-full bg-surface-3" role="progressbar" :aria-valuenow="migrationProgress" aria-valuemin="0" aria-valuemax="100">
          <div class="h-full rounded-full bg-primary transition-all duration-base" :style="{ width: `${migrationProgress}%` }"></div>
        </div>
        <p class="helper-text">进度 {{ migrationProgress }}%（已完成 / 总数）</p>

        <p v-if="migrationStatus.current_path" class="mt-2 truncate text-xs leading-5 text-ink-3">
          正在处理：<span class="text-ink-2">{{ migrationStatus.current_path }}</span>
        </p>
        <p v-if="migrationStatus.running" class="mt-2 text-xs leading-5 text-info">迁移进行中，进度每 2 秒自动刷新。</p>
        <p v-else-if="migrationStatus.paused" class="mt-2 text-xs leading-5 text-ink-2">迁移已暂停，可点「继续迁移」从断点恢复。</p>
        <p v-if="migrationFailed > 0" class="mt-2 text-xs leading-5 text-ink-2">
          有 {{ migrationFailed }} 个文件迁移失败，提示失败可在下轮重试（后端会自动重试尝试次数少于 3 的文件）。
        </p>
        <p v-if="migrationStatus.last_error" class="mt-2 text-xs leading-5 text-danger">{{ migrationStatus.last_error }}</p>

        <p class="mt-3 text-xs leading-5 text-ink-3">
          服务器现有图片 {{ migrationStatus.local_files }} 个，共 {{ formatBytes(migrationStatus.local_bytes) }}。
        </p>
      </div>

      <!-- E. 清理本地文件 -->
      <div class="mt-4 max-w-2xl rounded-tile border border-line-1 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">清理本地文件</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">
              迁移全部成功后才可执行。删除后本地不再保留副本，仅 COS 有；确认前会再提示一次。
            </p>
          </div>
          <button
            type="button"
            class="btn-danger min-h-[40px] !px-4 !py-2"
            :disabled="!canCleanup || cleanupBusy"
            @click="startCleanup"
          >
            {{ cleanupBusy ? '提交中...' : '清理已迁移的本地文件' }}
          </button>
        </div>
        <p v-if="!canCleanup && !cleanupRunning" class="mt-2 text-xs leading-5 text-ink-3">
          需先完成迁移：当前总数 {{ migrationTotal }}，已完成 {{ migrationDone }}，失败 {{ migrationFailed }}，待迁移 {{ migrationPending }}。
        </p>

        <div v-if="cleanupStatus.running || cleanupDeleted || cleanupFailed || cleanupStatus.last_error" class="mt-3 space-y-2">
          <div class="grid gap-3 text-xs leading-5 text-ink-3 sm:grid-cols-3">
            <p>已删除 <span class="tabular-nums text-success">{{ cleanupDeleted }}</span></p>
            <p>失败 <span class="tabular-nums" :class="cleanupFailed > 0 ? 'text-danger' : 'text-ink-1'">{{ cleanupFailed }}</span></p>
            <p>剩余 <span class="tabular-nums text-ink-1">{{ cleanupRemaining }}</span></p>
          </div>
          <p v-if="cleanupStatus.running" class="text-xs leading-5 text-info">清理进行中，进度每 2 秒自动刷新。</p>
          <p v-if="cleanupStatus.last_error" class="text-xs leading-5 text-danger">{{ cleanupStatus.last_error }}</p>
        </div>
      </div>

      <!-- F. 安全提示 -->
      <p class="mt-4 text-xs leading-5 text-ink-3">密钥加密保存在数据库，页面只显示打码后的尾号。</p>
    </div>
  </section>
</template>
