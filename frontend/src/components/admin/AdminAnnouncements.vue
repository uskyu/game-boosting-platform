<script setup>
import { onMounted, reactive, ref } from 'vue'

import api from '@/utils/api'

const emptyForm = () => ({
  title: '',
  content_html: '<p>请输入公告内容</p>',
  frequency: 'DAILY',
  is_enabled: false,
  priority: 0,
  start_at: '',
  end_at: '',
})

const announcements = ref([])
const loading = ref(false)
const saving = ref(false)
const previewing = ref(false)
const editingId = ref(null)
const previewHtml = ref('')
const notice = ref({ type: '', text: '' })
const form = reactive(emptyForm())

const frequencyOptions = [
  { value: 'DAILY', label: '每天首次打开' },
  { value: 'EVERY_OPEN', label: '每次打开页面' },
  { value: 'ONCE', label: '每条公告只显示一次' },
]

function messageClass(type) {
  return type === 'success' ? 'message-success' : 'message-error'
}

function formatDate(value) {
  if (!value) return '不限时间'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function toLocalInput(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (item) => String(item).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function toUtcIso(value) {
  return value ? new Date(value).toISOString() : null
}

async function load() {
  loading.value = true
  try {
    const response = await api.get('/admin/announcements')
    announcements.value = response.data || []
  } catch (error) {
    notice.value = { type: 'error', text: error.message || '公告加载失败' }
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
  previewHtml.value = ''
}

function editAnnouncement(item) {
  editingId.value = item.id
  Object.assign(form, {
    title: item.title,
    content_html: item.content_html,
    frequency: item.frequency,
    is_enabled: item.is_enabled,
    priority: item.priority,
    start_at: toLocalInput(item.start_at),
    end_at: toLocalInput(item.end_at),
  })
  previewHtml.value = item.safe_content_html || ''
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function preview() {
  previewing.value = true
  notice.value = { type: '', text: '' }
  try {
    const response = await api.post('/admin/announcements/preview', { content_html: form.content_html })
    previewHtml.value = response.data?.safe_content_html || ''
  } catch (error) {
    notice.value = { type: 'error', text: error.message || '公告预览失败' }
  } finally {
    previewing.value = false
  }
}

async function save() {
  if (!form.title.trim() || !form.content_html.trim()) {
    notice.value = { type: 'error', text: '请填写公告标题和内容' }
    return
  }
  saving.value = true
  notice.value = { type: '', text: '' }
  const payload = {
    title: form.title.trim(),
    content_html: form.content_html,
    frequency: form.frequency,
    is_enabled: form.is_enabled,
    priority: Number(form.priority) || 0,
    start_at: toUtcIso(form.start_at),
    end_at: toUtcIso(form.end_at),
  }
  try {
    const response = editingId.value
      ? await api.put(`/admin/announcements/${editingId.value}`, payload)
      : await api.post('/admin/announcements', payload)
    notice.value = { type: 'success', text: editingId.value ? '公告已更新' : '公告已创建' }
    editAnnouncement(response.data)
    await load()
  } catch (error) {
    notice.value = { type: 'error', text: error.message || '公告保存失败' }
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  if (!window.confirm(`确定删除公告“${item.title}”吗？`)) return
  try {
    await api.delete(`/admin/announcements/${item.id}`)
    notice.value = { type: 'success', text: '公告已删除' }
    if (editingId.value === item.id) resetForm()
    await load()
  } catch (error) {
    notice.value = { type: 'error', text: error.message || '公告删除失败' }
  }
}

onMounted(load)
</script>

<template>
  <section class="space-y-6">
    <article class="surface-card p-4 sm:p-6">
      <div class="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 class="text-2xl font-semibold text-ink-1">公告管理</h2>
          <p class="mt-2 text-sm text-ink-2">公告会在前台全局弹出；关闭后按照频率规则再次显示。</p>
        </div>
        <button v-if="editingId" type="button" class="btn-secondary !px-4 !py-2" @click="resetForm">新建公告</button>
      </div>

      <div v-if="notice.text" class="mt-4" :class="messageClass(notice.type)">{{ notice.text }}</div>

      <form class="mt-6 grid gap-5" @submit.prevent="save">
        <div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_220px_120px]">
          <div>
            <label class="label" for="announcement-title">公告标题</label>
            <input id="announcement-title" v-model="form.title" class="input min-h-[44px]" maxlength="200" required />
          </div>
          <div>
            <label class="label" for="announcement-frequency">显示频率</label>
            <select id="announcement-frequency" v-model="form.frequency" class="input min-h-[44px]">
              <option v-for="item in frequencyOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </div>
          <div>
            <label class="label" for="announcement-priority">优先级</label>
            <input id="announcement-priority" v-model="form.priority" class="input min-h-[44px]" type="number" min="-1000" max="1000" />
          </div>
        </div>

        <div class="grid gap-5 sm:grid-cols-2">
          <div>
            <label class="label" for="announcement-start">开始时间（可选）</label>
            <input id="announcement-start" v-model="form.start_at" class="input min-h-[44px]" type="datetime-local" />
          </div>
          <div>
            <label class="label" for="announcement-end">结束时间（可选）</label>
            <input id="announcement-end" v-model="form.end_at" class="input min-h-[44px]" type="datetime-local" />
          </div>
        </div>

        <div>
          <label class="label" for="announcement-content">HTML 内容</label>
          <textarea id="announcement-content" v-model="form.content_html" class="input min-h-48 resize-y font-mono text-sm" maxlength="20000" required></textarea>
          <p class="helper-text">尽量保留你粘贴的 HTML、class、内联样式和响应式 CSS；脚本、事件属性与危险链接会由服务端移除。</p>
        </div>

        <label class="inline-flex w-fit cursor-pointer items-center gap-3 text-sm text-ink-1">
          <input v-model="form.is_enabled" type="checkbox" class="h-4 w-4 accent-primary" />
          保存后立即启用
        </label>

        <div class="flex flex-wrap gap-3">
          <button type="submit" class="btn-primary min-h-[44px] !px-6" :disabled="saving">{{ saving ? '保存中...' : editingId ? '保存修改' : '创建公告' }}</button>
          <button type="button" class="btn-secondary min-h-[44px] !px-6" :disabled="previewing" @click="preview">{{ previewing ? '预览中...' : '预览安全效果' }}</button>
        </div>
      </form>

      <div v-if="previewHtml" class="mt-7 border-t border-line-1 pt-6">
        <p class="label">预览</p>
        <div class="announcement-content mt-3 rounded-tile bg-surface-2 p-5" v-html="previewHtml"></div>
      </div>
    </article>

    <article class="surface-card p-4 sm:p-6">
      <div class="flex items-center justify-between gap-4">
        <h3 class="text-xl font-semibold text-ink-1">已创建公告</h3>
        <span class="text-sm text-ink-3">{{ announcements.length }} 条</span>
      </div>

      <div v-if="loading" class="mt-5 space-y-3" aria-busy="true">
        <div v-for="n in 3" :key="n" class="skeleton h-20 !rounded-tile"></div>
      </div>
      <div v-else-if="!announcements.length" class="empty-state mt-5">
        <h4 class="empty-state__title">暂时没有公告</h4>
        <p class="empty-state__copy">创建并启用后，公告会在前台按规则显示。</p>
      </div>
      <div v-else class="mt-5 space-y-3">
        <article v-for="item in announcements" :key="item.id" class="info-tile flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h4 class="truncate font-semibold text-ink-1">{{ item.title }}</h4>
              <span :class="item.is_enabled ? 'tag !bg-success-soft !text-success' : 'tag !bg-surface-3 !text-ink-2'">{{ item.is_enabled ? '已启用' : '未启用' }}</span>
            </div>
            <p class="mt-1 text-xs text-ink-3">{{ frequencyOptions.find((option) => option.value === item.frequency)?.label }} · 优先级 {{ item.priority }} · {{ formatDate(item.start_at) }} 至 {{ formatDate(item.end_at) }}</p>
          </div>
          <div class="flex shrink-0 gap-2">
            <button type="button" class="btn-secondary !px-4 !py-2" @click="editAnnouncement(item)">编辑</button>
            <button type="button" class="btn-danger !px-4 !py-2" @click="remove(item)">删除</button>
          </div>
        </article>
      </div>
    </article>
  </section>
</template>
