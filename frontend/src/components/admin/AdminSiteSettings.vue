<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useSiteStore } from '@/stores/site'

const store = useSiteStore()
const form = reactive({
  site_name: '',
  site_description: '',
  hall_recent_claims_enabled: true,
  registration_captcha_enabled: true,
  accept_captcha_enabled: false,
  max_online_devices: 2,
})
const file = ref(null)
const notice = ref({ type: '', text: '' })

function sync() {
  form.site_name = store.settings.site_name
  form.site_description = store.settings.site_description || ''
  form.hall_recent_claims_enabled = store.settings.hall_recent_claims_enabled !== false
  // 注册验证码默认开、接单验证码默认关（与后端 SiteSettingResponse 一致）
  form.registration_captcha_enabled = store.settings.registration_captcha_enabled !== false
  form.accept_captcha_enabled = store.settings.accept_captcha_enabled === true
  form.max_online_devices = Number(store.settings.max_online_devices ?? 2)
}

// 后端约束 ge=0 le=20：输入框之外再夹一次，避免浏览器校验被绕过时提交 400
function normalizedMaxDevices() {
  const raw = form.max_online_devices
  const value = raw === '' || raw == null ? 2 : Number(raw)
  if (!Number.isFinite(value)) return 2
  return Math.min(Math.max(Math.trunc(value), 0), 20)
}

async function save() {
  const r = await store.updateSettings({
    site_name: form.site_name.trim(),
    site_description: form.site_description.trim() || null,
    hall_recent_claims_enabled: form.hall_recent_claims_enabled,
    registration_captcha_enabled: form.registration_captcha_enabled,
    accept_captcha_enabled: form.accept_captcha_enabled,
    max_online_devices: normalizedMaxDevices(),
  })
  notice.value = r.success ? { type: 'success', text: '站点设置已保存' } : { type: 'error', text: r.error }
  if (r.success) sync()
}

async function upload() {
  if (!file.value) return
  const r = await store.uploadLogo(file.value)
  notice.value = r.success
    ? { type: 'success', text: 'Logo 已上传，品牌已刷新' }
    : { type: 'error', text: r.error }
  file.value = null
}

async function remove() {
  const r = await store.removeLogo()
  notice.value = r.success
    ? { type: 'success', text: 'Logo 已删除' }
    : { type: 'error', text: r.error }
}

onMounted(async () => {
  // 管理员进入设置页时拉一次最新值：其它标签页可能刚改过安全开关
  await store.fetchSettings(true)
  sync()
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">站点管理</h2>
    <p class="mt-2 text-sm text-ink-2">维护平台对外展示的名称、简介与品牌 Logo。</p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">
      {{ notice.text }}
    </div>

    <form class="mt-6 grid max-w-2xl gap-5" @submit.prevent="save">
      <div>
        <label class="label" for="site-name">站点名称</label>
        <input id="site-name" v-model="form.site_name" required maxlength="200" class="input min-h-[44px]" />
      </div>

      <div>
        <label class="label" for="site-description">站点简介</label>
        <textarea id="site-description" v-model="form.site_description" maxlength="5000" rows="4" class="input resize-none"></textarea>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">大厅显示今日已接单</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">开启后，所有登录用户的大厅底部展示「今日已接单」区块（今天已被抢的单，含打手与保证金信息）；关闭后全站立即隐藏。</p>
          </div>
          <button
            type="button"
            :class="form.hall_recent_claims_enabled ? 'filter-pill-active' : 'filter-pill'"
            :aria-pressed="form.hall_recent_claims_enabled"
            @click="form.hall_recent_claims_enabled = !form.hall_recent_claims_enabled"
          >
            {{ form.hall_recent_claims_enabled ? '开启' : '已关闭' }}
          </button>
        </div>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-sm font-semibold text-ink-1">安全设置</p>
        <p class="mt-1 text-xs leading-5 text-ink-3">控制注册/接单验证码与同一账号的同时在线设备数。</p>

        <div class="mt-4 space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-medium text-ink-1">注册需要验证码</p>
              <p class="mt-1 text-xs leading-5 text-ink-3">关闭后注册页隐藏验证码，注册提交不再携带验证码字段。</p>
            </div>
            <button
              type="button"
              :class="form.registration_captcha_enabled ? 'filter-pill-active' : 'filter-pill'"
              :aria-pressed="form.registration_captcha_enabled"
              @click="form.registration_captcha_enabled = !form.registration_captcha_enabled"
            >
              {{ form.registration_captcha_enabled ? '开启' : '已关闭' }}
            </button>
          </div>

          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-medium text-ink-1">接单需要验证码</p>
              <p class="mt-1 text-xs leading-5 text-ink-3">开启后打手接单弹窗会要求输入图形验证码，每次接单验证码一次性使用。</p>
            </div>
            <button
              type="button"
              :class="form.accept_captcha_enabled ? 'filter-pill-active' : 'filter-pill'"
              :aria-pressed="form.accept_captcha_enabled"
              @click="form.accept_captcha_enabled = !form.accept_captcha_enabled"
            >
              {{ form.accept_captcha_enabled ? '开启' : '已关闭' }}
            </button>
          </div>

          <div>
            <label class="label" for="max-online-devices">最大同时在线设备数</label>
            <input
              id="max-online-devices"
              v-model.number="form.max_online_devices"
              type="number"
              min="0"
              max="20"
              step="1"
              class="input min-h-[44px] max-w-[160px]"
            />
            <p class="mt-1 text-xs leading-5 text-ink-3">0 表示不限制（最大 20）。超出时最早登录的设备会被下线并收到提示。</p>
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="store.loading">保存设置</button>
      </div>
    </form>

    <div class="mt-8 border-t border-line-1 pt-6">
      <label class="label" for="site-logo">站点 Logo</label>
      <div v-if="store.settings.site_logo_url" class="mb-4 flex items-center gap-4">
        <img :src="store.settings.site_logo_url" class="h-20 w-20 rounded-card object-cover" alt="站点 Logo" />
        <button type="button" class="btn-danger min-h-[44px] !px-4" @click="remove">删除 Logo</button>
      </div>
      <input id="site-logo" type="file" accept="image/png,image/jpeg,image/webp" class="input min-h-[44px]" @change="file = $event.target.files?.[0] || null" />
      <button type="button" class="btn-secondary mt-3 min-h-[44px] !px-5" :disabled="!file || store.loading" @click="upload">上传 Logo</button>
      <p class="mt-2 text-xs text-ink-3">推荐 512×512px PNG，透明背景，≤10MB。</p>
    </div>
  </section>
</template>
