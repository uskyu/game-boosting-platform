<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useSiteStore } from '@/stores/site'
import { useAdminUsersStore } from '@/stores/adminUsers'

const store = useSiteStore()
const adminUsersStore = useAdminUsersStore()
const form = reactive({
  site_name: '',
  site_description: '',
  hall_recent_claims_enabled: true,
  registration_captcha_enabled: true,
  accept_captcha_enabled: false,
  registration_default_can_publish: true,
  registration_default_can_accept: true,
  publish_permission_apply_enabled: true,
  publish_permission_required: true,
  max_online_devices: 2,
  order_title_replacements: [],
  footer_html: '',
})
const file = ref(null)
const notice = ref({ type: '', text: '' })
// 只有成功拉到后端设置后才允许保存，避免用默认 true 覆盖真实配置
const settingsLoaded = ref(false)

function sync() {
  form.site_name = store.settings.site_name
  form.site_description = store.settings.site_description || ''
  form.hall_recent_claims_enabled = store.settings.hall_recent_claims_enabled !== false
  // 注册验证码默认开、接单验证码默认关（与后端 SiteSettingResponse 一致）
  form.registration_captcha_enabled = store.settings.registration_captcha_enabled !== false
  form.accept_captcha_enabled = store.settings.accept_captcha_enabled === true
  // 注册权限默认开（生产站现状），申请入口默认开；只有后端明确 false 才关闭
  form.registration_default_can_publish = store.settings.registration_default_can_publish !== false
  form.registration_default_can_accept = store.settings.registration_default_can_accept !== false
  form.publish_permission_apply_enabled = store.settings.publish_permission_apply_enabled !== false
  // 全站发单管控默认开；只有后端明确 false 才视为全站开放
  form.publish_permission_required = store.settings.publish_permission_required !== false
  form.max_online_devices = Number(store.settings.max_online_devices ?? 2)
  form.order_title_replacements = (store.settings.order_title_replacements || []).map((rule) => ({
    original: rule.original || '',
    replacement: rule.replacement || '',
  }))
  form.footer_html = store.settings.footer_html || ''
}

// 预览只渲染「确定能保存成功」的内容：含脚本类片段时直接不渲染，
// 避免管理员在保存前就把危险 HTML 注入自己的后台页面
const footerPreview = computed(() => {
  const raw = form.footer_html.trim()
  if (!raw) return ''
  if (/<script|<iframe|<object|<embed|javascript:|\son[a-z]+\s*=/i.test(raw)) return ''
  return raw
})

const footerPreviewBlocked = computed(() => {
  const raw = form.footer_html.trim()
  return Boolean(raw) && !footerPreview.value
})

// 后端约束 ge=0 le=20：输入框之外再夹一次，避免浏览器校验被绕过时提交 400
function normalizedMaxDevices() {
  const raw = form.max_online_devices
  const value = raw === '' || raw == null ? 2 : Number(raw)
  if (!Number.isFinite(value)) return 2
  return Math.min(Math.max(Math.trunc(value), 0), 20)
}

async function save() {
  if (!settingsLoaded.value) {
    notice.value = { type: 'error', text: '站点设置尚未加载成功，请刷新后重试' }
    return
  }
  const replacements = form.order_title_replacements
    .map((rule) => ({
      original: (rule.original || '').trim(),
      replacement: rule.replacement || '',
    }))
    .filter((rule) => rule.original)
  const seen = new Set()
  for (const rule of replacements) {
    if (seen.has(rule.original)) {
      notice.value = { type: 'error', text: `关键词重复：${rule.original}` }
      return
    }
    seen.add(rule.original)
  }
  // 从「按发单权限管控」切到「全站开放」是不可忽略的放宽：保存前必须二次确认。
  const globalWasRequired = store.settings.publish_permission_required !== false
  if (globalWasRequired && !form.publish_permission_required) {
    const confirmed = window.confirm(
      '关闭全站发单管控后，所有未被封禁的普通用户（包括「未开通发单」的用户）都可以立即发单。'
      + '「未开通」标记会保留，重新开启管控后恢复限制；个人封禁仍然生效。确认关闭？',
    )
    if (!confirmed) return
  }
  const r = await store.updateSettings({
    site_name: form.site_name.trim(),
    site_description: form.site_description.trim() || null,
    hall_recent_claims_enabled: form.hall_recent_claims_enabled,
    registration_captcha_enabled: form.registration_captcha_enabled,
    accept_captcha_enabled: form.accept_captcha_enabled,
    // 布尔值必须显式发送（包括 false），后端按全量表单保存
    registration_default_can_publish: form.registration_default_can_publish,
    registration_default_can_accept: form.registration_default_can_accept,
    publish_permission_apply_enabled: form.publish_permission_apply_enabled,
    publish_permission_required: form.publish_permission_required,
    max_online_devices: normalizedMaxDevices(),
    order_title_replacements: replacements,
    footer_html: form.footer_html.trim(),
  })
  notice.value = r.success ? { type: 'success', text: '站点设置已保存' } : { type: 'error', text: r.error }
  if (r.success) {
    settingsLoaded.value = true
    sync()
  }
}

function addReplacement() {
  if (form.order_title_replacements.length >= 50) {
    notice.value = { type: 'error', text: '最多配置 50 条替换规则' }
    return
  }
  form.order_title_replacements.push({ original: '', replacement: '' })
}

function removeReplacement(index) {
  form.order_title_replacements.splice(index, 1)
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

// ── 一键收回全部普通用户发单权限（不是封禁）──
const bulkRevoking = ref(false)

function bulkRevokeConfirmText(preview) {
  const intro = preview.success
    ? `将收回全部 ${Number(preview.data?.eligible ?? 0)} 名启用中普通用户的发单权限（管理员、禁用用户不受影响）。`
    : '人数获取失败。将收回全部启用中普通用户的发单权限（管理员、禁用用户不受影响）。'
  return `${intro}\n\n这只是收回「已开通」标记，不是封禁：全站发单管控关闭时，未被封禁的用户仍可发单；重新开启管控后，这些用户需要重新申请或由管理员开通；个人封禁仍然生效。\n\n确认收回？`
}

async function revokeAllPublishPermissions() {
  if (bulkRevoking.value) return
  bulkRevoking.value = true
  try {
    // 先取受影响人数；预览失败不阻断，确认文案退化为通用人数说明
    const preview = await adminUsersStore.fetchBulkPreview()
    if (!window.confirm(bulkRevokeConfirmText(preview))) return
    const result = await adminUsersStore.bulkPublishPermission({
      scope: 'all_non_admin',
      can_publish: false,
    })
    if (!result.success) {
      notice.value = { type: 'error', text: result.error || '收回发单权限失败' }
      return
    }
    const updated = Number(result.data?.updated ?? 0)
    const skipped = Number(result.data?.skipped ?? 0)
    notice.value = {
      type: 'success',
      text: `已收回 ${updated} 名普通用户的发单权限${skipped ? `，跳过 ${skipped} 人` : ''}。`
        + '这不是封禁：只收回「已开通」标记；全站管控关闭时未被封禁用户仍可发单，重新开启后需重新申请或由管理员开通；被封禁用户始终不能发单。',
    }
  } finally {
    bulkRevoking.value = false
  }
}

onMounted(async () => {
  // 管理员进入设置页时拉一次最新值：其它标签页可能刚改过安全开关
  const result = await store.fetchSettings(true)
  if (!result.success) {
    // 拉取失败不 sync：默认 true 可能覆盖后端真实配置
    notice.value = { type: 'error', text: result.error || '加载站点设置失败，请刷新后重试' }
    return
  }
  settingsLoaded.value = true
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

      <!-- 全站发单权限管控：决定普通用户发单是否需要个人开通 -->
      <div class="rounded-tile border border-line-1 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="min-w-0">
            <p class="text-sm font-semibold text-ink-1">全站发单权限管控</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">保存后立即生效，对所有普通用户（含老用户）生效；管理员发单不受影响。</p>
          </div>
          <button
            type="button"
            :class="form.publish_permission_required ? 'filter-pill-active' : 'filter-pill'"
            :aria-pressed="form.publish_permission_required"
            @click="form.publish_permission_required = !form.publish_permission_required"
          >
            {{ form.publish_permission_required ? '管控开启' : '全站开放' }}
          </button>
        </div>

        <p v-if="form.publish_permission_required" class="mt-3 rounded-tile border border-dashed border-line-1 p-3 text-xs leading-5 text-ink-2">
          当前按发单权限管控；未开通的普通用户不能发单，可申请或由管理员开通；被封禁用户始终不能发单。
        </p>
        <p v-else class="mt-3 rounded-tile border border-dashed border-line-1 p-3 text-xs leading-5 text-ink-2">
          全站开放；所有未被封禁的普通用户都可以发单；未开通标记保留，重新开启后恢复限制；个人封禁仍生效。
        </p>

        <div class="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            class="btn-secondary min-h-[44px] !px-4"
            :disabled="bulkRevoking"
            @click="revokeAllPublishPermissions"
          >
            {{ bulkRevoking ? '收回中...' : '一键收回全部普通用户发单权限' }}
          </button>
          <p class="text-xs leading-5 text-ink-3">只收回「已开通」标记，不是封禁；封禁与解封仍请在「用户管理」逐人操作。</p>
        </div>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-sm font-semibold text-ink-1">注册权限</p>
        <p class="mt-1 text-xs leading-5 text-ink-3">管理新用户注册时的默认权限，以及无发单权限用户的自助申请入口。</p>

        <div class="mt-4 space-y-4">
          <!-- 前两个默认开关只作用于注册后的新用户 -->
          <div class="rounded-tile border border-dashed border-line-1 p-3">
            <p class="text-xs leading-5 text-ink-3">
              只影响注册后的新用户，老用户权限保持不变；管理员发单不受这里限制。关闭发单后，可在「用户管理」单独或批量开通。现有用户的发单权限由上方「全站发单权限管控」统一决定。
            </p>

            <div class="mt-3 space-y-4">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p class="text-sm font-medium text-ink-1">新注册用户默认允许发单</p>
                  <p class="mt-1 text-xs leading-5 text-ink-3">关闭后新用户注册即可接单，发单需管理员在「用户管理」单独或批量开通。</p>
                </div>
                <button
                  type="button"
                  :class="form.registration_default_can_publish ? 'filter-pill-active' : 'filter-pill'"
                  :aria-pressed="form.registration_default_can_publish"
                  @click="form.registration_default_can_publish = !form.registration_default_can_publish"
                >
                  {{ form.registration_default_can_publish ? '开启' : '已关闭' }}
                </button>
              </div>

              <div class="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p class="text-sm font-medium text-ink-1">新注册用户默认允许接单</p>
                  <p class="mt-1 text-xs leading-5 text-ink-3">关闭后新用户注册后不能接单，需要管理员在「用户管理」单独开通。</p>
                </div>
                <button
                  type="button"
                  :class="form.registration_default_can_accept ? 'filter-pill-active' : 'filter-pill'"
                  :aria-pressed="form.registration_default_can_accept"
                  @click="form.registration_default_can_accept = !form.registration_default_can_accept"
                >
                  {{ form.registration_default_can_accept ? '开启' : '已关闭' }}
                </button>
              </div>
            </div>
          </div>

          <!-- 自助申请开关：立即影响现有无发单权限用户，与注册默认值无关 -->
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-medium text-ink-1">开启发单权限自助申请</p>
              <p class="mt-1 text-xs leading-5 text-ink-3">开启后无发单权限的用户可在「发单权限」页提交申请，立即对所有无发单权限的现有用户生效；关闭后用户只能联系管理员开通。</p>
            </div>
            <button
              type="button"
              :class="form.publish_permission_apply_enabled ? 'filter-pill-active' : 'filter-pill'"
              :aria-pressed="form.publish_permission_apply_enabled"
              @click="form.publish_permission_apply_enabled = !form.publish_permission_apply_enabled"
            >
              {{ form.publish_permission_apply_enabled ? '开启' : '已关闭' }}
            </button>
          </div>
        </div>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">订单标题关键词替换</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">发单/改单时自动把标题里的原文替换为右侧文本，聊天里的订单卡片同步生效；默认把「名牌」「明牌」替换为「【MP】」。留空替换文本表示删除该关键词。</p>
          </div>
          <button type="button" class="btn-secondary min-h-[40px] !px-4 !py-2" @click="addReplacement">添加规则</button>
        </div>

        <div v-if="!form.order_title_replacements.length" class="mt-3 rounded-tile border border-dashed border-line-1 px-3 py-4 text-xs text-ink-3">
          当前不替换任何关键词。点击「添加规则」新增一条。
        </div>
        <div
          v-for="(rule, index) in form.order_title_replacements"
          :key="`title-rule-${index}`"
          class="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center"
        >
          <input
            v-model="rule.original"
            type="text"
            maxlength="50"
            class="input min-h-[40px] sm:flex-1"
            placeholder="原文，如：名牌"
            :aria-label="`第 ${index + 1} 条规则原文`"
          />
          <span class="hidden text-ink-3 sm:inline" aria-hidden="true">→</span>
          <input
            v-model="rule.replacement"
            type="text"
            maxlength="100"
            class="input min-h-[40px] sm:flex-1"
            placeholder="替换为，如：【MP】"
            :aria-label="`第 ${index + 1} 条规则替换文本`"
          />
          <button type="button" class="btn-ghost !min-h-[40px] !px-3 !py-1.5 text-danger" @click="removeReplacement(index)">删除</button>
        </div>
        <p class="mt-3 text-xs leading-5 text-ink-3">最多 50 条；同一原文不能重复。保存后仅影响新发/编辑的订单和之后生成的聊天订单卡片。</p>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-sm font-semibold text-ink-1">页脚备案信息（HTML）</p>
        <p class="mt-1 text-xs leading-5 text-ink-3">
          显示在所有页面最底部，通常填 ICP 备案号并链接到工信部备案系统。支持 HTML，留空则不显示。
        </p>
        <textarea
          id="footer-html"
          v-model="form.footer_html"
          rows="3"
          maxlength="2000"
          class="input mt-3 resize-y font-mono text-xs leading-6"
          placeholder='示例：<a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">粤ICP备2026000000号</a>'
        ></textarea>
        <p class="mt-2 text-xs leading-5 text-ink-3">
          不能包含 &lt;script&gt;、&lt;iframe&gt;、onclick 等脚本内容；保存后立即全站生效。
        </p>
        <div v-if="footerPreview" class="mt-3 rounded-tile border border-dashed border-line-1 px-3 py-2 text-center text-xs leading-6 text-ink-3">
          <span class="mr-2 opacity-70">预览</span><span v-html="footerPreview"></span>
        </div>
        <p v-else-if="footerPreviewBlocked" class="mt-3 text-xs leading-5 text-danger">
          内容包含脚本类片段，保存会被拒绝，请先删除相关代码。
        </p>
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
