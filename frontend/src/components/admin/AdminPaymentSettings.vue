<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useWalletStore } from '@/stores/wallet'

const store = useWalletStore()
const form = reactive({ enabled: false, pay_address: '', epay_id: '', epay_key: '', notify_base_url: '', pay_methods: '', min_amount: '1.00' })
const notice = ref({ type: '', text: '' })
const saving = ref(false)
const hasKey = computed(() => Boolean(store.paymentSettings?.has_key))
const keyPlaceholder = computed(() => (hasKey.value ? '已配置，留空表示不修改' : '请输入商户密钥'))

function sync(data) {
  const settings = data || store.paymentSettings || {}
  form.enabled = Boolean(settings.enabled)
  form.pay_address = settings.pay_address || ''
  form.epay_id = settings.epay_id || ''
  form.epay_key = ''
  form.notify_base_url = settings.notify_base_url || ''
  form.pay_methods = settings.pay_methods || ''
  form.min_amount = settings.min_amount != null ? String(settings.min_amount) : '1.00'
}

// pay_methods 后端要求合法 JSON 数组文本；空字符串表示不限制
function validatePayMethods() {
  const text = form.pay_methods.trim()
  if (!text) return ''
  try {
    const parsed = JSON.parse(text)
    return Array.isArray(parsed) ? '' : '支付方式必须是合法的 JSON 数组'
  } catch {
    return '支付方式必须是合法的 JSON 数组'
  }
}

async function save() {
  notice.value = { type: '', text: '' }
  const payMethodsError = validatePayMethods()
  if (payMethodsError) {
    notice.value = { type: 'error', text: payMethodsError }
    return
  }
  const minAmount = Number(form.min_amount)
  if (!Number.isFinite(minAmount) || minAmount <= 0) {
    notice.value = { type: 'error', text: '最低充值金额需大于 0' }
    return
  }
  saving.value = true
  const result = await store.updatePaymentSettings({
    enabled: form.enabled,
    pay_address: form.pay_address.trim() || null,
    epay_id: form.epay_id.trim() || null,
    epay_key: form.epay_key.trim() || null,
    notify_base_url: form.notify_base_url.trim() || null,
    pay_methods: form.pay_methods.trim() || null,
    min_amount: minAmount,
  })
  saving.value = false
  if (result.success) {
    notice.value = { type: 'success', text: '支付设置已保存' }
    sync(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '保存失败' }
  }
}

onMounted(async () => {
  const result = await store.fetchPaymentSettings()
  if (result.success) {
    sync(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '加载支付设置失败' }
  }
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">支付设置</h2>
    <p class="mt-2 text-sm text-ink-2">配置易支付（Epay）接口，用于用户自助充值入账。</p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">{{ notice.text }}</div>

    <form class="mt-6 grid max-w-2xl gap-5" @submit.prevent="save">
      <label class="flex min-h-[44px] items-center gap-2 text-sm text-ink-1">
        <input v-model="form.enabled" type="checkbox" />
        启用易支付充值
      </label>

      <div>
        <label class="label" for="pay-address">支付接口地址</label>
        <input id="pay-address" v-model="form.pay_address" type="text" class="input min-h-[44px]" placeholder="https://yourdomain.com" />
      </div>

      <div>
        <label class="label" for="epay-id">商户 ID（PID）</label>
        <input id="epay-id" v-model="form.epay_id" type="text" class="input min-h-[44px]" placeholder="请输入易支付商户 PID" />
      </div>

      <div>
        <label class="label" for="epay-key">商户密钥</label>
        <input id="epay-key" v-model="form.epay_key" type="password" autocomplete="new-password" class="input min-h-[44px]" :placeholder="keyPlaceholder" />
        <p class="mt-2 text-xs text-ink-3">出于安全考虑密钥不会回显；留空表示保持现有密钥不变。</p>
      </div>

      <div>
        <label class="label" for="notify-base-url">回调基础地址</label>
        <input id="notify-base-url" v-model="form.notify_base_url" type="text" class="input min-h-[44px]" placeholder="https://yourdomain.com" />
        <p class="mt-2 text-xs text-ink-3">留空则自动使用当前访问地址；生产环境建议显式填写公网 HTTPS 地址。</p>
      </div>

      <div>
        <label class="label" for="pay-methods">支付方式</label>
        <textarea id="pay-methods" v-model="form.pay_methods" rows="3" class="input resize-none" placeholder='[{"name":"支付宝","type":"alipay"}]'></textarea>
        <p class="mt-2 text-xs text-ink-3">必须是 JSON 数组文本，例如 <code>[{"name":"支付宝","type":"alipay"}]</code>；留空表示不限制。</p>
      </div>

      <div>
        <label class="label" for="min-amount">最低充值金额（元）</label>
        <input id="min-amount" v-model="form.min_amount" type="number" min="0.01" step="0.01" class="input min-h-[44px]" placeholder="1.00" />
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="store.paymentSettingsLoading || saving">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
      </div>
    </form>
  </section>
</template>
