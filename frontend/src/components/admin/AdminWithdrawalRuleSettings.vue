<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import {
  REFRESH_MODE_META,
  WITHDRAWAL_INTERVAL_MAX_HOURS,
  WITHDRAWAL_INTERVAL_MIN_HOURS,
  describeRefreshRule,
} from '@/utils/withdrawRules'
import { useWithdrawalRuleStore } from '@/stores/withdrawalRule'

const store = useWithdrawalRuleStore()
const form = reactive({
  mode: 'INTERVAL',
  interval_hours: '24',
})
const notice = ref({ type: '', text: '' })
const saving = ref(false)

// 切到每日刷新只隐藏输入框不丢值：老板在两个模式间来回试时不用重填小时数
const MODE_OPTIONS = [
  { value: 'INTERVAL', label: REFRESH_MODE_META.INTERVAL.label },
  { value: 'DAILY_NOON', label: REFRESH_MODE_META.DAILY_NOON.label },
]
const HOUR_SHORTCUTS = ['6', '12', '24']

const currentRuleText = computed(() => describeRefreshRule(store.settings || form))

function sync(data) {
  const settings = data || store.settings || {}
  form.mode = settings.mode === 'DAILY_NOON' ? 'DAILY_NOON' : 'INTERVAL'
  form.interval_hours = String(settings.interval_hours ?? 24)
}

async function save() {
  notice.value = { type: '', text: '' }
  const hours = Number(form.interval_hours)
  // 两种模式都把小时间隔一并发给后端：切回 INTERVAL 时值还在，服务端校验也按同一区间
  if (!Number.isInteger(hours) || hours < WITHDRAWAL_INTERVAL_MIN_HOURS || hours > WITHDRAWAL_INTERVAL_MAX_HOURS) {
    notice.value = {
      type: 'error',
      text: `刷新间隔需为 ${WITHDRAWAL_INTERVAL_MIN_HOURS}-${WITHDRAWAL_INTERVAL_MAX_HOURS} 之间的整数小时`,
    }
    return
  }
  saving.value = true
  const result = await store.updateSettings({ mode: form.mode, interval_hours: hours })
  saving.value = false
  if (result.success) {
    notice.value = { type: 'success', text: '提现规则已保存，立即生效' }
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
    notice.value = { type: 'error', text: result.error || '加载提现规则失败' }
  }
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">提现规则</h2>
    <p class="mt-2 text-sm text-ink-2">
      每个刷新周期内每个用户只有 1 次提现机会；被驳回的申请不占机会。<span class="font-semibold text-ink-1">修改后立即生效</span>，正在等待中的用户按新规则重新计算机会。
    </p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">{{ notice.text }}</div>

    <form class="mt-6 grid max-w-2xl gap-5" @submit.prevent="save">
      <div>
        <p class="label">刷新模式</p>
        <div class="flex flex-wrap items-center gap-2">
          <button
            v-for="option in MODE_OPTIONS"
            :key="option.value"
            type="button"
            class="filter-pill"
            :class="{ 'filter-pill-active': form.mode === option.value }"
            @click="form.mode = option.value"
          >
            {{ option.label }}
          </button>
        </div>
        <p class="mt-1.5 text-xs text-ink-3">
          模式 A「每隔 N 小时刷新」：每个用户自上次提现时间起算，时隔 N 小时恢复 1 次机会，互不影响。
        </p>
        <p class="mt-1 text-xs text-ink-3">
          模式 B「每天 12:00 刷新」：全站统一在每天 12:00 刷新，窗口为今天 12:00 到明天 12:00。
        </p>
      </div>

      <div>
        <label class="label" for="withdrawal-rule-interval-hours">刷新间隔（小时）</label>
        <div class="flex flex-wrap items-center gap-2">
          <input
            id="withdrawal-rule-interval-hours"
            v-model="form.interval_hours"
            type="number"
            min="1"
            max="168"
            step="1"
            class="input max-w-[160px] min-h-[44px]"
            :class="{ 'opacity-50': form.mode !== 'INTERVAL' }"
            placeholder="例如：24"
            :disabled="form.mode !== 'INTERVAL'"
          />
          <button
            v-for="shortcut in HOUR_SHORTCUTS"
            :key="shortcut"
            type="button"
            class="filter-pill"
            :disabled="form.mode !== 'INTERVAL'"
            @click="form.interval_hours = shortcut"
          >
            {{ shortcut }} 小时
          </button>
        </div>
        <p class="mt-1.5 text-xs text-ink-3">仅模式 A 生效，可选 {{ WITHDRAWAL_INTERVAL_MIN_HOURS }}-{{ WITHDRAWAL_INTERVAL_MAX_HOURS }} 小时；切到每日刷新时这里保留，方便切回。</p>
      </div>

      <div class="rounded-tile border border-line-1 p-4">
        <p class="text-sm font-semibold text-ink-1">规则示例</p>
        <p class="mt-2 text-sm text-ink-2">
          模式 A（间隔 24 小时）：用户 9/27 10:00 提现 → 9/28 10:00 恢复机会。
        </p>
        <p class="mt-1 text-sm text-ink-2">
          模式 B（每天 12:00）：窗口为今天 12:00 到明天 12:00，用户 9/27 11:30 提现 → 当天 12:00 就能再提；9/27 12:01 提现 → 等 9/28 12:00。
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="store.loading || saving">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
        <span class="text-sm text-ink-2">当前规则：<span class="font-semibold text-price">{{ currentRuleText }}</span></span>
      </div>
    </form>
  </section>
</template>
