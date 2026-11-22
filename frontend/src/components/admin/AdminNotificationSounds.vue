<script setup>
/**
 * 后台「通知音频」设置：四个固定槽位（来新订单 / 订单被接手 / 订单申请取消 /
 * 聊天消息）各自支持 上传 MP3/WAV、试听、音量、静音、恢复内置。
 *
 * 接口（直接走 api，不新建 store）：
 * - GET  /admin/sounds         读取四槽位配置（后端已归一化，必有全部槽位）
 * - PUT  /admin/sounds         保存指定槽位（未提交的槽位保持原值）
 * - POST /admin/sounds/upload  multipart slot + file
 * - POST /admin/sounds/reset   把某槽位恢复内置音频（音量/静音保留）
 *
 * 约定：上传/恢复默认成功后后端会主动广播，在线客户端重拉站点设置即可生效；
 * 音量与开关走 PUT 保存，交互保持简单——滑块松手（change）才提交一次。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import api from '@/utils/api'

const SLOTS = [
  { key: 'new_order', label: '来新订单', builtin: '/sounds/new-order.wav' },
  { key: 'claimed', label: '订单被接手', builtin: '/sounds/order-claimed.wav' },
  { key: 'cancel_requested', label: '订单申请取消', builtin: '/sounds/order-cancel-requested.wav' },
  // 聊天消息的内置默认是 WebAudio 合成双音，没有静态文件可播放/试听
  { key: 'chat_message', label: '聊天消息', builtin: null },
]

const sounds = reactive({})
const loading = ref(true)
const saving = ref('')
const fileInputs = reactive({})
// 文件 input 的 DOM 引用（上传后要手动清空 value，才能再次选同一个文件）。
// 用普通对象而不是 reactive：避免 Vue 把原生 DOM 元素包成响应式代理。
const fileInputRefs = {}
const notice = ref({ type: '', text: '' })
let previewAudio = null

// 先用默认值占满四个槽位：加载中/失败时模板里 sounds[slot].volume 也不会读到 undefined
applySounds(null)

function slotState(key) {
  const slot = sounds[key]
  if (!slot) return '使用内置音频'
  if (!slot.enabled) return '已静音'
  return slot.url ? '已自定义' : '使用内置音频'
}

function isDefault(key) {
  return !sounds[key]?.url
}

function formatError(error) {
  const detail = error?.message || error?.detail
  return typeof detail === 'string' && detail ? detail : '操作失败，请稍后重试'
}

function applySounds(payload) {
  const incoming = payload?.sounds && typeof payload.sounds === 'object' ? payload.sounds : {}
  SLOTS.forEach((slot) => {
    const value = incoming[slot.key] || {}
    sounds[slot.key] = {
      url: value.url || null,
      volume: Number.isFinite(Number(value.volume)) ? Number(value.volume) : 80,
      enabled: value.enabled !== false,
    }
  })
}

async function load() {
  loading.value = true
  try {
    const r = await api.get('/admin/sounds')
    applySounds(r.data)
  } catch (error) {
    notice.value = { type: 'error', text: formatError(error) }
  } finally {
    loading.value = false
  }
}

async function saveSlot(key, payload = sounds[key]) {
  saving.value = key
  try {
    const r = await api.put('/admin/sounds', { sounds: { [key]: payload } })
    applySounds(r.data)
    notice.value = { type: 'success', text: `「${slotLabel(key)}」设置已保存` }
  } catch (error) {
    notice.value = { type: 'error', text: formatError(error) }
    // 保存失败时回拉一次，避免界面停留在一个没落库的值
    await load()
  } finally {
    saving.value = ''
  }
}

function slotLabel(key) {
  return SLOTS.find((slot) => slot.key === key)?.label || key
}

function onVolumeChange(key) {
  const slot = sounds[key]
  if (!slot) return
  const volume = Math.min(100, Math.max(0, Math.round(Number(slot.volume) || 0)))
  slot.volume = volume
  saveSlot(key)
}

function toggleEnabled(key) {
  const slot = sounds[key]
  if (!slot) return
  slot.enabled = !slot.enabled
  saveSlot(key)
}

async function upload(key) {
  const input = fileInputRefs[key]
  const file = input?.files?.[0] || fileInputs[key]
  if (!file || saving.value) return
  const form = new FormData()
  form.append('slot', key)
  form.append('file', file)
  saving.value = key
  try {
    const r = await api.post('/admin/sounds/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    applySounds(r.data)
    notice.value = { type: 'success', text: `「${slotLabel(key)}」音频已上传并启用` }
  } catch (error) {
    notice.value = { type: 'error', text: formatError(error) }
  } finally {
    saving.value = ''
    // 清空 input，便于重复选择同一个文件（否则 change 不会触发）
    fileInputs[key] = null
    if (input) input.value = ''
  }
}

async function reset(key) {
  if (isDefault(key)) return
  saving.value = key
  try {
    const r = await api.post('/admin/sounds/reset', { slot: key })
    applySounds(r.data)
    notice.value = { type: 'success', text: `「${slotLabel(key)}」已恢复内置音频` }
  } catch (error) {
    notice.value = { type: 'error', text: formatError(error) }
  } finally {
    saving.value = ''
  }
}

function stopPreview() {
  if (!previewAudio) return
  try {
    previewAudio.pause()
    previewAudio.currentTime = 0
  } catch {
    // 忽略
  }
  previewAudio = null
}

// 试听当前生效音频：自定义 URL 优先，否则播内置文件；聊天消息内置是合成音，
// 没有静态文件时保持按钮禁用并提示。
function preview(key) {
  stopPreview()
  const slot = sounds[key]
  const meta = SLOTS.find((item) => item.key === key)
  const src = slot?.url || meta?.builtin
  if (!src) {
    notice.value = { type: 'error', text: '聊天消息内置提示音是浏览器合成的短音，没有可试听的音频文件' }
    return
  }
  try {
    const audio = new Audio(src)
    audio.volume = Math.min(1, Math.max(0, Number(slot?.volume ?? 80) / 100))
    previewAudio = audio
    const result = audio.play()
    if (result && typeof result.catch === 'function') {
      result.catch(() => {
        notice.value = { type: 'error', text: '试听失败，请确认音频文件可访问' }
      })
    }
  } catch {
    notice.value = { type: 'error', text: '当前浏览器不支持音频播放' }
  }
}

const canPreview = computed(() => (key) => Boolean(sounds[key]?.url || SLOTS.find((s) => s.key === key)?.builtin))

onMounted(load)
onBeforeUnmount(stopPreview)
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">通知音频</h2>
    <p class="mt-2 text-sm text-ink-2">
      为四类通知配置提示音：可上传自定义 MP3/WAV（≤2MB），调节音量或静音；恢复默认后使用内置音频。
      保存后在线用户即时生效（页面会自动刷新站点设置）。
    </p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">
      {{ notice.text }}
    </div>

    <div v-if="loading" class="mt-6 space-y-3" aria-busy="true">
      <div v-for="n in 4" :key="`sound-skeleton-${n}`" class="skeleton h-32 !rounded-card"></div>
    </div>

    <div v-else class="mt-6 grid gap-4 lg:grid-cols-2">
      <article v-for="meta in SLOTS" :key="meta.key" class="rounded-tile border border-line-1 p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="text-base font-semibold text-ink-1">{{ meta.label }}</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">
              当前状态：<span class="font-medium text-ink-2">{{ slotState(meta.key) }}</span>
            </p>
            <p v-if="sounds[meta.key]?.url" class="mt-1 break-all text-xs text-ink-3">{{ sounds[meta.key].url }}</p>
            <p v-else class="mt-1 text-xs leading-5 text-ink-3">
              {{ meta.builtin ? '内置订单语音文件' : '内置浏览器合成提示音（无音频文件）' }}
            </p>
          </div>
          <button
            type="button"
            :class="sounds[meta.key]?.enabled ? 'filter-pill-active' : 'filter-pill'"
            :aria-pressed="Boolean(sounds[meta.key]?.enabled)"
            :disabled="saving === meta.key"
            @click="toggleEnabled(meta.key)"
          >
            {{ sounds[meta.key]?.enabled ? '已开启' : '已静音' }}
          </button>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-2">
          <input
            :ref="(el) => { fileInputRefs[meta.key] = el }"
            type="file"
            accept=".mp3,.wav,audio/mpeg,audio/wav"
            class="input min-h-[44px] w-full sm:w-auto sm:flex-1"
            :aria-label="`${meta.label} 音频文件`"
            :disabled="saving === meta.key"
            @change="fileInputs[meta.key] = $event.target.files?.[0] || null"
          />
          <button
            type="button"
            class="btn-secondary min-h-[44px] !px-4"
            :disabled="!fileInputs[meta.key] || saving === meta.key"
            @click="upload(meta.key)"
          >
            {{ saving === meta.key ? '处理中...' : '上传' }}
          </button>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-3">
          <label class="flex min-w-0 flex-1 items-center gap-2 text-xs text-ink-3">
            <span class="shrink-0">音量</span>
            <input
              v-model.number="sounds[meta.key].volume"
              type="range"
              min="0"
              max="100"
              step="1"
              class="min-w-0 flex-1"
              :aria-label="`${meta.label} 音量`"
              :disabled="saving === meta.key"
              @change="onVolumeChange(meta.key)"
            />
            <span class="w-10 shrink-0 text-right tabular-nums text-ink-2">{{ sounds[meta.key]?.volume }}%</span>
          </label>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="btn-secondary min-h-[40px] !px-4 !py-2"
            :disabled="!canPreview(meta.key) || saving === meta.key"
            @click="preview(meta.key)"
          >
            试听
          </button>
          <button
            type="button"
            class="btn-ghost min-h-[40px] !px-4 !py-2"
            :disabled="isDefault(meta.key) || saving === meta.key"
            @click="reset(meta.key)"
          >
            恢复默认
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
