<script setup>
/**
 * 后台「保证金管理」。
 * 配置保证金模式开关、结账计时起点、转回冷却天数、默认炸单赔付金，
 * 以及可编辑的权益阶梯表（整表提交，门槛不可重复）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useWalletStore } from '@/stores/wallet'

const store = useWalletStore()

const SETTLEMENT_MODE_OPTIONS = [
  { value: 'AFTER_DELIVERY', label: '打手交付后开始计时（到期自动通过）' },
  { value: 'AFTER_APPROVAL', label: '老板审核通过后开始计时' },
]

const form = reactive({
  enabled: true,
  return_cooldown_days: 7,
  default_compensation: 20,
  settlement_mode: 'AFTER_DELIVERY',
  global_booster_quota: 5,
  tiers: [],
})
const notice = ref({ type: '', text: '' })
const saving = ref(false)

function cloneTiers(tiers) {
  return (Array.isArray(tiers) ? tiers : []).map((tier) => ({
    threshold: tier.threshold != null ? Number(tier.threshold) : 0,
    wait_seconds: Number(tier.wait_seconds) || 0,
    exempt_compensation: Boolean(tier.exempt_compensation),
    // null 必须保留为 null（= 该档不承诺加速，按订单到账时效倒计时）；
    // 不能退化成 0 —— 0 小时的语义是「交付即结算」，会造成提前打款。
    settle_hours: tier.settle_hours == null ? null : Number(tier.settle_hours),
    // null = 不限；输入框清空时是 ''，保存时统一归一
    daily_accept_limit: tier.daily_accept_limit == null ? null : Number(tier.daily_accept_limit),
    enabled: tier.enabled !== false,
  }))
}

function sync(data) {
  const settings = data || store.depositSettings || {}
  form.enabled = settings.enabled !== false
  form.return_cooldown_days = settings.return_cooldown_days != null ? Number(settings.return_cooldown_days) : 7
  form.default_compensation = settings.default_compensation != null ? Number(settings.default_compensation) : 20
  form.settlement_mode = settings.settlement_mode || 'AFTER_DELIVERY'
  form.global_booster_quota = settings.global_booster_quota != null ? Number(settings.global_booster_quota) : 5
  form.tiers = cloneTiers(settings.tiers)
}

function addTier() {
  form.tiers.push({ threshold: 0, wait_seconds: 30, exempt_compensation: false, settle_hours: 72, daily_accept_limit: null, enabled: true })
}

function removeTier(index) {
  form.tiers.splice(index, 1)
}

// 门槛重复检测（按数值比对，"100" 与 "100.00" 视为重复）
function hasDuplicateThreshold() {
  const seen = new Set()
  for (const tier of form.tiers) {
    const key = Number(tier.threshold)
    if (!Number.isFinite(key)) return false
    if (seen.has(key)) return true
    seen.add(key)
  }
  return false
}

async function save() {
  notice.value = { type: '', text: '' }

  const cooldown = Number(form.return_cooldown_days)
  if (!Number.isInteger(cooldown) || cooldown < 0 || cooldown > 365) {
    notice.value = { type: 'error', text: '保证金转回冷却天数需为 0-365 的整数' }
    return
  }

  const compensation = Number(form.default_compensation)
  if (!Number.isFinite(compensation) || compensation < 0) {
    notice.value = { type: 'error', text: '默认炸单赔付金需为不小于 0 的金额' }
    return
  }

  const quota = Number(form.global_booster_quota)
  if (!Number.isInteger(quota) || quota < 1 || quota > 1000) {
    notice.value = { type: 'error', text: '全局接单配额需为 1-1000 的整数' }
    return
  }

  for (const tier of form.tiers) {
    if (!Number.isFinite(Number(tier.threshold)) || Number(tier.threshold) < 0) {
      notice.value = { type: 'error', text: '保证金门槛需为不小于 0 的金额' }
      return
    }
    const wait = Number(tier.wait_seconds)
    if (!Number.isInteger(wait) || wait < 0) {
      notice.value = { type: 'error', text: '接单等待秒数需为不小于 0 的整数' }
      return
    }
    // 结账时效：留空（null / ''）= 该档不承诺加速，按订单到账时效倒计时；
    // 其余须为不小于 0 的整数小时
    const settleRaw = tier.settle_hours
    if (settleRaw !== null && settleRaw !== '') {
      const settle = Number(settleRaw)
      if (!Number.isInteger(settle) || settle < 0) {
        notice.value = { type: 'error', text: '结账时效需为不小于 0 的整数（小时，留空 = 按订单到账时效）' }
        return
      }
    }
    // 每日接单量：留空（null/''）或 0 = 不限；其余须为 0-999 整数
    const daily = tier.daily_accept_limit
    if (daily !== null && daily !== '') {
      const dailyValue = Number(daily)
      if (!Number.isInteger(dailyValue) || dailyValue < 0 || dailyValue > 999) {
        notice.value = { type: 'error', text: '每日接单量上限需为 0-999 的整数（留空 = 不限）' }
        return
      }
    }
  }

  if (hasDuplicateThreshold()) {
    notice.value = { type: 'error', text: '保证金阶梯的门槛不能重复' }
    return
  }

  // 这里**故意不做**「阶梯必须含 0 元档」的校验（2026-10-18 与后端一并撤除）。
  // 老板有意删掉 0 元档：原始默认 0 元档 exempt_compensation=False，会让
  // 0 保证金打手接单时必须冻结赔付金（余额 0 即接不了单）；删掉后 0 保证金
  // 打手兜底到最低档、继承免冻结才接得上单。保留该校验会把后台保存卡死。

  saving.value = true
  const result = await store.updateDepositSettings({
    enabled: form.enabled,
    return_cooldown_days: cooldown,
    default_compensation: compensation,
    settlement_mode: form.settlement_mode,
    global_booster_quota: quota,
    tiers: form.tiers.map((tier) => ({
      threshold: Number(tier.threshold),
      wait_seconds: Number(tier.wait_seconds),
      exempt_compensation: Boolean(tier.exempt_compensation),
      // 留空 = 按订单到账时效（发 null；不要发 0，0 小时会被当成立即结算）
      settle_hours:
        tier.settle_hours === '' || tier.settle_hours == null
          ? null
          : Number(tier.settle_hours),
      // 0 与空串同义 = 不限，统一发 null（后端同样归一）
      daily_accept_limit:
        tier.daily_accept_limit === '' || tier.daily_accept_limit == null
          ? null
          : Number(tier.daily_accept_limit),
      enabled: Boolean(tier.enabled),
    })),
  })
  saving.value = false

  if (result.success) {
    notice.value = { type: 'success', text: '保证金设置已保存' }
    sync(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '保存失败' }
  }
}

onMounted(async () => {
  const result = await store.fetchDepositSettings()
  if (result.success) {
    sync(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '加载保证金设置失败' }
  }
})
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <h2 class="text-2xl font-semibold text-ink-1">保证金管理</h2>
    <p class="mt-2 text-sm text-ink-2">配置保证金模式与权益阶梯：门槛越高，接单等待越短、结账越快、越可能免除炸单赔付金，每日可接单量上限越高。</p>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">{{ notice.text }}</div>

    <form class="mt-6 grid max-w-3xl gap-5" @submit.prevent="save">
      <label class="flex min-h-[44px] items-center gap-2 text-sm text-ink-1">
        <input v-model="form.enabled" type="checkbox" />
        开启保证金模式
      </label>

      <div>
        <label class="label" for="settlement-mode">结账时效计时起点</label>
        <select id="settlement-mode" v-model="form.settlement_mode" class="input min-h-[44px]">
          <option v-for="option in SETTLEMENT_MODE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>

      <div class="grid gap-5 sm:grid-cols-2">
        <div>
          <label class="label" for="return-cooldown-days">保证金转回冷却天数</label>
          <input
            id="return-cooldown-days"
            v-model.number="form.return_cooldown_days"
            type="number"
            min="0"
            max="365"
            step="1"
            class="input min-h-[44px]"
            placeholder="0-365"
          />
          <p class="mt-1.5 text-xs text-ink-3">全部订单完成后需满该天数才可转回余额。</p>
        </div>

        <div>
          <label class="label" for="default-compensation">订单默认炸单赔付金（元）</label>
          <input
            id="default-compensation"
            v-model.number="form.default_compensation"
            type="number"
            min="0"
            step="0.01"
            class="input min-h-[44px]"
            placeholder="例如 20"
          />
        </div>

        <div>
          <label class="label" for="global-quota">全局同时接单上限</label>
          <input
            id="global-quota"
            v-model.number="form.global_booster_quota"
            type="number"
            min="1"
            max="1000"
            step="1"
            class="input min-h-[44px]"
            placeholder="例如 5"
          />
          <p class="mt-1.5 text-xs text-ink-3">所有用户最多同时处理 N 个未完成订单。</p>
        </div>
      </div>

      <!-- 权益阶梯：整表替换，保存时提交完整 tiers 数组 -->
      <div>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <label class="label !mb-0">权益阶梯</label>
          <button type="button" class="btn-secondary min-h-[40px] !px-4 !py-2" @click="addTier">添加一行</button>
        </div>
        <p class="mt-1.5 text-xs text-ink-3">门槛不能重复；保存时以当前表格整表替换后台配置。</p>
        <p class="mt-1.5 text-xs text-ink-3">
          结账时效<strong>留空表示该档不承诺时长</strong>，改按打手所接订单设置的到账时效倒计时；
          填写了小时数就<strong>以档位为准</strong>（后台设置为基准，订单时效只在留空时兜底）。
          填 0 表示<strong>交付即结算</strong>，与留空含义完全不同，别混用。
        </p>
        <p class="mt-1.5 text-xs text-ink-3">
          「接单免冻结」只影响<strong>接单时是否预冻结</strong>赔付金：勾选后该档位打手接单不再预冻结，
          真正炸单时赔付金<strong>直接从保证金里扣</strong>，并不是免除赔付责任。
        </p>

        <div v-if="!form.tiers.length" class="empty-state mt-4">
          <div class="empty-state__icon" aria-hidden="true">🪜</div>
          <h3 class="empty-state__title">暂无阶梯</h3>
          <p class="empty-state__copy">点击「添加一行」新增第一档权益。</p>
        </div>

        <div v-else class="mt-4 overflow-x-auto">
          <table class="w-full min-w-[720px] border-collapse text-left text-sm">
            <thead>
              <tr class="text-xs uppercase tracking-[0.08em] text-ink-3">
                <th class="px-2 py-2 font-medium">保证金门槛（元）</th>
                <th class="px-2 py-2 font-medium">接单等待（秒）</th>
                <th class="px-2 py-2 font-medium">每日接单量（0点刷新）</th>
                <th class="px-2 py-2 font-medium">接单免冻结赔付金</th>
                <th class="px-2 py-2 font-medium">结账时效（小时，留空=按订单）</th>
                <th class="px-2 py-2 font-medium">启用</th>
                <th class="px-2 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(tier, index) in form.tiers" :key="index" class="border-t border-line-1">
                <td class="px-2 py-2">
                  <input v-model.number="tier.threshold" type="number" min="0" step="0.01" class="input h-10 w-28" />
                </td>
                <td class="px-2 py-2">
                  <input v-model.number="tier.wait_seconds" type="number" min="0" step="1" class="input h-10 w-24" />
                </td>
                <td class="px-2 py-2">
                  <input
                    v-model="tier.daily_accept_limit"
                    type="number"
                    min="0"
                    max="999"
                    step="1"
                    placeholder="留空=不限"
                    aria-label="每日接单量上限"
                    class="input h-10 w-24"
                  />
                </td>
                <td class="px-2 py-2">
                  <label class="flex min-h-[40px] items-center gap-2 text-sm text-ink-2">
                    <input v-model="tier.exempt_compensation" type="checkbox" />
                    {{ tier.exempt_compensation ? '免冻结' : '需冻结' }}
                  </label>
                </td>
                <td class="px-2 py-2">
                  <input
                    v-model="tier.settle_hours"
                    type="number"
                    min="0"
                    step="1"
                    placeholder="留空=按订单"
                    aria-label="结账时效（小时，留空按订单到账时效）"
                    class="input h-10 w-24"
                  />
                </td>
                <td class="px-2 py-2">
                  <input v-model="tier.enabled" type="checkbox" aria-label="启用该档位" />
                </td>
                <td class="px-2 py-2">
                  <button type="button" class="btn-ghost !px-3 !py-1.5 text-danger" @click="removeTier(index)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn-primary min-h-[44px] !px-5" :disabled="store.depositSettingsLoading || saving">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
      </div>
    </form>
  </section>
</template>
