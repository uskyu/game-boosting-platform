<script setup>
import { onMounted, reactive, ref } from 'vue'

import { useWithdrawalSettingsStore } from '@/stores/withdrawalSettings'

const store = useWithdrawalSettingsStore()
const form = reactive({
  alipay_enabled: true,
  wechat_enabled: true,
})
const notice = ref({ type: '', text: '' })
const saving = ref(false)

// 两个开关行：文案 + 当前状态 + 一行效果说明（active  pill = 开启，非 active = 已关闭）
const CHANNEL_TOGGLES = [
  {
    key: 'alipay_enabled',
    name: '支付宝提现',
    hint: '关闭后，提现表单禁选支付宝，服务端同步拒绝该渠道的新申请。',
  },
  {
    key: 'wechat_enabled',
    name: '微信提现',
    hint: '关闭后，提现表单禁选微信，服务端同步拒绝该渠道的新申请。',
  },
]

function sync(data) {
  const settings = data || store.settings || {}
  form.alipay_enabled = settings.alipay_enabled !== false
  form.wechat_enabled = settings.wechat_enabled !== false
}

async function save() {
  notice.value = { type: '', text: '' }
  saving.value = true
  const result = await store.updateSettings({ ...form })
  saving.value = false
  if (result.success) {
    notice.value = { type: 'success', text: '已保存，立即生效' }
    sync(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '保存失败' }
  }
}

onMounted(async () => {
  const result = await store.fetchSettings(true)
  if (result.success) {
    sync(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '加载提现分类设置失败' }
  }
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">提现分类</h2>
    <p class="mt-2 text-sm text-ink-2">
      控制提现表单里的收款渠道开关：关闭后用户不能再选该渠道发起提现。<span class="font-semibold text-ink-1">修改后立即生效</span>，处理中的提现单不受影响。
    </p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">{{ notice.text }}</div>

    <form class="mt-6 grid max-w-2xl gap-5" @submit.prevent="save">
      <div
        v-for="toggle in CHANNEL_TOGGLES"
        :key="toggle.key"
        class="rounded-tile border border-line-1 p-4"
      >
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-ink-1">{{ toggle.name }}</p>
            <p class="mt-1 text-xs leading-5 text-ink-3">{{ toggle.hint }}</p>
          </div>
          <button
            type="button"
            :class="form[toggle.key] ? 'filter-pill-active' : 'filter-pill'"
            :aria-pressed="form[toggle.key]"
            @click="form[toggle.key] = !form[toggle.key]"
          >
            {{ form[toggle.key] ? '开启' : '已关闭' }}
          </button>
        </div>
      </div>

      <div class="message-info">
        关闭后，用户不能再选择该渠道发起新的提现申请（表单禁用 + 服务端拒绝）；处理中的提现单不受影响，仍可正常审核、驳回与打款；银行卡渠道不受此开关控制。
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="store.loading || saving">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
        <span class="text-sm text-ink-2">
          当前状态：支付宝<span :class="form.alipay_enabled ? 'text-success' : 'text-danger'">{{ form.alipay_enabled ? '开启' : '已关闭' }}</span> · 微信<span :class="form.wechat_enabled ? 'text-success' : 'text-danger'">{{ form.wechat_enabled ? '开启' : '已关闭' }}</span>
        </span>
      </div>
    </form>
  </section>
</template>
