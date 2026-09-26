<script setup>
import { onMounted, reactive, ref } from 'vue'

import { formatFeeRate, formatMoneyFixed, serviceFeeBreakdown } from '@/utils/display'
import { useServiceFeeStore } from '@/stores/serviceFee'

const store = useServiceFeeStore()
const form = reactive({
  service_fee_rate: '0',
})
const notice = ref({ type: '', text: '' })
const saving = ref(false)

const currentRate = ref(0)
const previewPrice = ref(150)

function sync(data) {
  const settings = data || store.settings || {}
  currentRate.value = Number(settings.service_fee_rate ?? 0)
  form.service_fee_rate = String(settings.service_fee_rate ?? '0')
}

// 以示例金额预览三段拆分，让老板保存前对“抽多少”有直观概念
const preview = () => {
  const breakdown = serviceFeeBreakdown(previewPrice.value, Number(form.service_fee_rate))
  return {
    rate: formatFeeRate(breakdown.ratePercent),
    fee: formatMoneyFixed(breakdown.fee),
    net: formatMoneyFixed(breakdown.net),
  }
}

async function save() {
  notice.value = { type: '', text: '' }
  const rate = Number(form.service_fee_rate)
  if (!Number.isFinite(rate) || rate < 0 || rate > 100) {
    notice.value = { type: 'error', text: '服务费费率需为 0-100 之间的数值' }
    return
  }
  saving.value = true
  const result = await store.updateSettings({ service_fee_rate: rate })
  saving.value = false
  if (result.success) {
    notice.value = { type: 'success', text: '服务费设置已保存' }
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
    notice.value = { type: 'error', text: result.error || '加载服务费设置失败' }
  }
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">服务费设置</h2>
    <p class="mt-2 text-sm text-ink-2">
      设置全局服务费费率后，管理员发布订单时打开「服务费」开关即按此费率收取，无需每单手输；开关底下会显示当前费率。仍可为某单手输自定义费率。
    </p>
    <p class="mt-1.5 text-xs leading-5 text-ink-3">
      注意：订单发布瞬间费率即固定，之后调整全局费率只影响之后发布的订单，已发布订单的收费规则不变。
    </p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">{{ notice.text }}</div>

    <form class="mt-6 grid max-w-2xl gap-5" @submit.prevent="save">
      <div>
        <label class="label" for="global-service-fee-rate">全局服务费费率（%）</label>
        <div class="flex flex-wrap items-center gap-2">
          <input
            id="global-service-fee-rate"
            v-model="form.service_fee_rate"
            type="number"
            min="0"
            max="100"
            step="0.1"
            class="input max-w-[160px] min-h-[44px]"
            placeholder="例如：8"
          />
          <button
            v-for="shortcut in ['5', '8', '10']"
            :key="shortcut"
            type="button"
            class="filter-pill"
            @click="form.service_fee_rate = shortcut"
          >
            {{ shortcut }}%
          </button>
        </div>
        <p class="mt-1.5 text-xs text-ink-3">填 0 表示不收取。打手实际到账 = 订单金额 × (1 - 费率)。</p>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-sm font-semibold text-ink-1">效果预览（以 ¥{{ previewPrice }} 订单为例）</p>
        <p class="mt-2 text-sm tabular-nums text-ink-2">
          订单金额 ¥{{ previewPrice }} · 服务费 {{ preview.rate }} -{{ preview.fee }} · 打手实际到账
          <span class="font-semibold text-price">{{ preview.net }}</span>
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="store.loading || saving">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
        <span v-if="currentRate > 0" class="text-sm text-ink-2">当前全局服务费：<span class="font-semibold text-price">{{ currentRate }}%</span></span>
        <span v-else class="text-sm text-ink-3">当前未设置全局服务费（不收取）</span>
      </div>
    </form>
  </section>
</template>
