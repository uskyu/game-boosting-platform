<script setup>
/**
 * 后台「支付设置」：全局开关 + 多支付网关管理。
 *
 * 页面分三块：
 * 1. 全局设置：充值开关、最低充值金额（PUT 只提交这两项）；
 * 2. 网关列表：每个网关可原地编辑，实时回显最终提交地址；
 * 3. 新增网关：默认展开空表单，创建后需手动「设为当前使用」。
 *
 * 提交地址的规范化规则与后端 payment_gateway_service.normalize_submit_url 保持一致：
 * 勾选「自动补 /submit.php」时按基地址理解，未勾选时按完整入口原样使用。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useWalletStore } from '@/stores/wallet'

const store = useWalletStore()

const notice = ref({ type: '', text: '' })
const savingSettings = ref(false)
// 同一时刻只允许一个网关写操作，避免并发改到同一份配置
const busyKey = ref('')

const globalForm = reactive({ enabled: true, min_amount: '' })

// { [gatewayId]: 可编辑草稿 }：编辑只改草稿，保存成功后才用接口返回值覆盖
const drafts = reactive({})
const gatewayList = ref([])

const activeGatewayName = computed(
  () => gatewayList.value.find((gateway) => gateway.is_active)?.name || ''
)

function emptyDraft() {
  return {
    name: '',
    submit_url: '',
    append_submit_php: true,
    merchant_id: '',
    secret_key: '',
    query_url: '',
    alipay_enabled: true,
    wxpay_enabled: true,
    enabled: true,
  }
}

const newGateway = reactive(emptyDraft())

function toDraft(gateway) {
  return {
    name: gateway.name || '',
    submit_url: gateway.submit_url || '',
    append_submit_php: gateway.append_submit_php !== false,
    merchant_id: gateway.merchant_id || '',
    secret_key: '',
    query_url: gateway.query_url || '',
    alipay_enabled: gateway.alipay_enabled !== false,
    wxpay_enabled: gateway.wxpay_enabled !== false,
    enabled: gateway.enabled !== false,
  }
}

function syncGlobal(data) {
  const settings = data || {}
  globalForm.enabled = settings.enabled !== false
  globalForm.min_amount = settings.min_amount != null ? String(settings.min_amount) : ''
}

// 首次加载 / 新建网关后重建整份草稿
function applyGateways(list) {
  gatewayList.value = Array.isArray(list) ? list : []
  for (const key of Object.keys(drafts)) delete drafts[key]
  for (const gateway of gatewayList.value) {
    drafts[gateway.id] = toDraft(gateway)
  }
}

function replaceGateway(updated) {
  if (!updated || updated.id == null) return
  const index = gatewayList.value.findIndex((gateway) => gateway.id === updated.id)
  if (index >= 0) {
    gatewayList.value[index] = updated
  } else {
    gatewayList.value.push(updated)
  }
  drafts[updated.id] = toDraft(updated)
}

// ── 地址预览（与后端规则一致）──

function resolvePreview(rawSubmitUrl, appendSubmitPhp) {
  const raw = (rawSubmitUrl || '').trim()
  if (!raw) return ''

  // 后端只保留 https://域名+路径（丢弃查询参数/锚点），这里同样处理
  let base = raw
  if (/^https:\/\//i.test(raw)) {
    try {
      const parsed = new URL(raw)
      base = `https://${parsed.host}${parsed.pathname}`
    } catch {
      base = raw
    }
  }
  base = base.replace(/\/+$/, '')

  if (!appendSubmitPhp) return base

  const lowered = base.toLowerCase()
  if (lowered.endsWith('.php')) return base
  if (lowered.endsWith('/submit')) return `${base}.php`
  return `${base}/submit.php`
}

function submitPreview(gateway) {
  const draft = drafts[gateway.id]
  return resolvePreview(draft?.submit_url, draft?.append_submit_php) || gateway.resolved_submit_url || '—'
}

// ── 校验（后端仍会校验，这里只是提前拦）──

function checkHttpsUrl(raw, field) {
  const value = (raw || '').trim()
  if (!value) return `请填写${field}`
  if (!/^https:\/\//i.test(value)) return `${field}必须以 https:// 开头`

  let parsed
  try {
    parsed = new URL(value)
  } catch {
    return `${field}格式不正确，例如 https://api.example.com/gateway/submit`
  }
  if (!parsed.hostname) return `${field}缺少域名`
  // 只有域名、没有路径：几乎一定是漏填了支付目录
  if (!parsed.pathname || parsed.pathname === '/') {
    return `${field}缺少支付路径，例如 /xpay/epay 或 /gateway/submit`
  }
  if (parsed.search || parsed.hash) return `${field}不能带查询参数或锚点`
  return ''
}

function validateGateway(draft, { requireSecret }) {
  if (!(draft.name || '').trim()) return '请填写网关名称'
  if (!(draft.merchant_id || '').trim()) return '请填写商户号'
  if (requireSecret && !(draft.secret_key || '').trim()) return '请填写商户密钥'
  if (!draft.alipay_enabled && !draft.wxpay_enabled) return '请至少勾选一种支付方式'

  const submitError = checkHttpsUrl(draft.submit_url, '提交地址')
  if (submitError) return submitError

  const queryUrl = (draft.query_url || '').trim()
  if (queryUrl) {
    const queryError = checkHttpsUrl(queryUrl, '查询地址')
    if (queryError) return queryError
  }
  return ''
}

function buildPayload(draft, { includeSecret = false } = {}) {
  const payload = {
    name: (draft.name || '').trim(),
    submit_url: (draft.submit_url || '').trim(),
    append_submit_php: Boolean(draft.append_submit_php),
    merchant_id: (draft.merchant_id || '').trim(),
    query_url: (draft.query_url || '').trim() || null,
    alipay_enabled: Boolean(draft.alipay_enabled),
    wxpay_enabled: Boolean(draft.wxpay_enabled),
    enabled: Boolean(draft.enabled),
  }
  // 编辑时密钥留空 = 保持原密钥，干脆不带该字段
  const secret = (draft.secret_key || '').trim()
  if (includeSecret || secret) payload.secret_key = secret
  return payload
}

// ── 全局设置 ──

async function saveGlobalSettings() {
  notice.value = { type: '', text: '' }
  const minAmount = Number(globalForm.min_amount)
  if (!Number.isFinite(minAmount) || minAmount <= 0) {
    notice.value = { type: 'error', text: '最低充值金额需大于 0' }
    return
  }

  savingSettings.value = true
  const result = await store.updatePaymentSettings({
    enabled: globalForm.enabled,
    min_amount: minAmount,
  })
  savingSettings.value = false

  if (result.success) {
    notice.value = { type: 'success', text: '全局设置已保存' }
    syncGlobal(result.data)
  } else {
    notice.value = { type: 'error', text: result.error || '保存失败' }
  }
}

// ── 网关操作 ──

async function loadSettings() {
  const result = await store.fetchPaymentSettings()
  if (result.success) {
    syncGlobal(result.data)
    applyGateways(result.data?.gateways)
  } else {
    notice.value = { type: 'error', text: result.error || '加载支付设置失败' }
  }
  return result
}

async function saveGateway(gateway) {
  const draft = drafts[gateway.id]
  if (!draft) return

  notice.value = { type: '', text: '' }
  const error = validateGateway(draft, { requireSecret: false })
  if (error) {
    notice.value = { type: 'error', text: `网关「${gateway.name}」：${error}` }
    return
  }

  busyKey.value = `save-${gateway.id}`
  const result = await store.updatePaymentGateway(gateway.id, buildPayload(draft))
  busyKey.value = ''

  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '保存失败' }
    return
  }
  notice.value = { type: 'success', text: `网关「${result.data?.name || draft.name}」已保存` }
  replaceGateway(result.data)
}

async function activateGateway(gateway) {
  if (gateway.is_active) return

  notice.value = { type: '', text: '' }
  busyKey.value = `activate-${gateway.id}`
  const result = await store.activatePaymentGateway(gateway.id)
  busyKey.value = ''

  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '切换失败' }
    return
  }

  // 后端保证同时只有一个 is_active；这里同步本地标记，不覆盖正在编辑的草稿
  const updated = result.data
  gatewayList.value = gatewayList.value.map((item) =>
    item.id === gateway.id
      ? { ...item, is_active: true, enabled: updated?.enabled != null ? Boolean(updated.enabled) : item.enabled }
      : { ...item, is_active: false }
  )
  notice.value = {
    type: 'success',
    text: `已切换到「${updated?.name || gateway.name}」，只影响之后的新订单`,
  }
}

async function deleteGateway(gateway) {
  if (!window.confirm(`确定删除网关「${gateway.name}」吗？删除后无法恢复。`)) return

  notice.value = { type: '', text: '' }
  busyKey.value = `delete-${gateway.id}`
  const result = await store.deletePaymentGateway(gateway.id)
  busyKey.value = ''

  if (!result.success) {
    // 当前使用中 / 已被订单引用时后端返回 400，直接把原因显示出来
    notice.value = { type: 'error', text: result.error || '删除失败' }
    return
  }

  gatewayList.value = gatewayList.value.filter((item) => item.id !== gateway.id)
  delete drafts[gateway.id]
  notice.value = { type: 'success', text: `网关「${gateway.name}」已删除` }
}

async function createGateway() {
  notice.value = { type: '', text: '' }
  const error = validateGateway(newGateway, { requireSecret: true })
  if (error) {
    notice.value = { type: 'error', text: error }
    return
  }

  busyKey.value = 'create'
  const result = await store.createPaymentGateway(buildPayload(newGateway, { includeSecret: true }))
  busyKey.value = ''

  if (!result.success) {
    notice.value = { type: 'error', text: result.error || '新增网关失败' }
    return
  }

  notice.value = {
    type: 'success',
    text: `网关「${result.data?.name || newGateway.name}」已创建，点「设为当前使用」后才会用于新订单`,
  }
  Object.assign(newGateway, emptyDraft())
  await loadSettings()
}

onMounted(loadSettings)
</script>

<template>
  <section class="surface-card p-4 sm:p-6">
    <header class="flex flex-col gap-1">
      <h2 class="text-2xl font-semibold text-ink-1">支付设置</h2>
      <p class="text-sm text-ink-2">
        配置用户自助充值入账所使用的支付通道。回调地址无需填写（系统按访问地址自动回传）；切换网关只影响之后的新订单。
      </p>
    </header>

    <div v-if="notice.text" class="mt-4" :class="notice.type === 'success' ? 'message-success' : 'message-error'">
      {{ notice.text }}
    </div>

    <!-- ── 全局设置 ── -->
    <form class="mt-6 max-w-2xl" @submit.prevent="saveGlobalSettings">
      <h3 class="text-lg font-semibold text-ink-1">全局设置</h3>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div class="sm:col-span-2">
          <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
            <input v-model="globalForm.enabled" type="checkbox" />
            启用充值功能
          </label>
          <p class="mt-1 text-xs text-ink-3">关闭后用户在我的钱包看不到充值入口，已有订单仍会按回调结果入账。</p>
        </div>
        <div>
          <label class="label" for="payment-min-amount">最低充值金额（元）</label>
          <input
            id="payment-min-amount"
            v-model="globalForm.min_amount"
            type="number"
            inputmode="decimal"
            min="0.01"
            step="0.01"
            class="input min-h-[44px]"
            placeholder="例如 10.00"
          />
        </div>
      </div>
      <div class="mt-4">
        <button
          type="submit"
          class="btn-primary min-h-[44px] !px-5"
          :disabled="savingSettings || store.paymentSettingsLoading || Boolean(busyKey)"
        >{{ savingSettings ? '保存中...' : '保存全局设置' }}</button>
      </div>
    </form>

    <!-- ── 支付网关 ── -->
    <div class="mt-8">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <h3 class="text-lg font-semibold text-ink-1">支付网关</h3>
        <p class="text-xs text-ink-3">
          {{ activeGatewayName ? `当前使用：${activeGatewayName}` : '尚未启用任何网关' }}
        </p>
      </div>
      <p class="mt-1 text-xs text-ink-3">
        勾选「自动补 /submit.php」时提交地址按基地址理解（例如 /xpay/epay → /xpay/epay/submit.php）；不勾选时按填写的完整入口使用。
      </p>

      <p v-if="!gatewayList.length" class="mt-4 text-sm text-ink-2">
        还没有配置任何支付网关，先在下方新增一个。
      </p>

      <div v-else class="mt-4 space-y-3">
        <article
          v-for="gateway in gatewayList"
          :key="gateway.id"
          class="rounded-tile border border-line-1 p-4"
        >
          <template v-if="drafts[gateway.id]">
            <header class="flex flex-wrap items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <h4 class="text-base font-semibold text-ink-1">
                    {{ drafts[gateway.id].name || '未命名网关' }}
                  </h4>
                  <span v-if="gateway.is_active" class="tag !bg-success-soft !text-success">当前使用</span>
                  <span v-if="!drafts[gateway.id].enabled" class="tag !bg-surface-3 !text-ink-2">已停用</span>
                </div>
                <p class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
                  <span>商户号 {{ drafts[gateway.id].merchant_id || '—' }}</span>
                  <span v-if="drafts[gateway.id].alipay_enabled" class="tag !bg-surface-3 !text-ink-2">支付宝</span>
                  <span v-if="drafts[gateway.id].wxpay_enabled" class="tag !bg-surface-3 !text-ink-2">微信</span>
                </p>
              </div>
            </header>

            <p class="mt-2 break-all font-mono text-xs text-ink-2">
              实际提交到：<span class="text-ink-1">{{ submitPreview(gateway) }}</span>
            </p>
            <p v-if="gateway.supports_query === false" class="mt-1 text-[11px] text-ink-3">
              该网关不支持主动查询，支付记录里的「同步状态」会返回不支持。
            </p>

            <div class="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <label class="label" :for="`gateway-name-${gateway.id}`">网关名称</label>
                <input
                  :id="`gateway-name-${gateway.id}`"
                  v-model="drafts[gateway.id].name"
                  type="text"
                  class="input min-h-[44px]"
                  placeholder="例如：立收通"
                />
              </div>
              <div>
                <label class="label" :for="`gateway-merchant-${gateway.id}`">商户号</label>
                <input
                  :id="`gateway-merchant-${gateway.id}`"
                  v-model="drafts[gateway.id].merchant_id"
                  type="text"
                  class="input min-h-[44px]"
                  placeholder="上游商户号 / PID"
                />
              </div>

              <div class="sm:col-span-2">
                <label class="label" :for="`gateway-submit-${gateway.id}`">提交地址</label>
                <input
                  :id="`gateway-submit-${gateway.id}`"
                  v-model="drafts[gateway.id].submit_url"
                  type="url"
                  inputmode="url"
                  class="input min-h-[44px] font-mono text-xs"
                  placeholder="https://api.example.com/gateway/submit"
                />
                <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
                  <input v-model="drafts[gateway.id].append_submit_php" type="checkbox" />
                  自动补 /submit.php（易支付标准布局）
                </label>
              </div>

              <div>
                <label class="label" :for="`gateway-secret-${gateway.id}`">商户密钥</label>
                <input
                  :id="`gateway-secret-${gateway.id}`"
                  v-model="drafts[gateway.id].secret_key"
                  type="password"
                  autocomplete="new-password"
                  class="input min-h-[44px]"
                  :placeholder="gateway.has_key ? '已配置，留空不修改' : '请输入商户密钥'"
                />
              </div>
              <div>
                <label class="label" :for="`gateway-query-${gateway.id}`">查询地址（可选）</label>
                <input
                  :id="`gateway-query-${gateway.id}`"
                  v-model="drafts[gateway.id].query_url"
                  type="url"
                  inputmode="url"
                  class="input min-h-[44px] font-mono text-xs"
                  placeholder="可留空，易支付布局自动使用 api.php"
                />
              </div>

              <div class="sm:col-span-2">
                <label class="label">支付方式</label>
                <div class="flex flex-wrap items-center gap-x-5">
                  <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
                    <input v-model="drafts[gateway.id].alipay_enabled" type="checkbox" />
                    支付宝
                  </label>
                  <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
                    <input v-model="drafts[gateway.id].wxpay_enabled" type="checkbox" />
                    微信
                  </label>
                  <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
                    <input v-model="drafts[gateway.id].enabled" type="checkbox" />
                    启用该网关
                  </label>
                </div>
                <p class="mt-1 text-xs text-ink-3">至少勾选一种支付方式；停用的网关不会用于新订单。</p>
              </div>
            </div>

            <div class="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                class="btn-primary min-h-[44px] !px-4"
                :disabled="Boolean(busyKey) || savingSettings"
                @click="saveGateway(gateway)"
              >{{ busyKey === `save-${gateway.id}` ? '保存中...' : '保存' }}</button>
              <button
                type="button"
                class="btn-secondary min-h-[44px] !px-4"
                :disabled="gateway.is_active || Boolean(busyKey)"
                @click="activateGateway(gateway)"
              >{{ busyKey === `activate-${gateway.id}` ? '切换中...' : '设为当前使用' }}</button>
              <button
                type="button"
                class="btn-ghost min-h-[44px] !px-4 text-danger"
                :disabled="Boolean(busyKey)"
                @click="deleteGateway(gateway)"
              >{{ busyKey === `delete-${gateway.id}` ? '删除中...' : '删除' }}</button>
            </div>
          </template>
        </article>
      </div>
    </div>

    <!-- ── 新增网关 ── -->
    <form class="mt-8 border-t border-line-1 pt-6" @submit.prevent="createGateway">
      <h3 class="text-lg font-semibold text-ink-1">新增网关</h3>
      <p class="mt-1 text-xs text-ink-3">
        新建后不会自动成为当前使用，需要点该网关卡片上的「设为当前使用」。商户密钥必填，保存后不再回显。
      </p>

      <div class="mt-4 grid max-w-2xl gap-4 sm:grid-cols-2">
        <div>
          <label class="label" for="new-gateway-name">网关名称</label>
          <input
            id="new-gateway-name"
            v-model="newGateway.name"
            type="text"
            class="input min-h-[44px]"
            placeholder="例如：立收通"
          />
        </div>
        <div>
          <label class="label" for="new-gateway-merchant">商户号</label>
          <input
            id="new-gateway-merchant"
            v-model="newGateway.merchant_id"
            type="text"
            class="input min-h-[44px]"
            placeholder="上游商户号 / PID"
          />
        </div>

        <div class="sm:col-span-2">
          <label class="label" for="new-gateway-submit">提交地址</label>
          <input
            id="new-gateway-submit"
            v-model="newGateway.submit_url"
            type="url"
            inputmode="url"
            class="input min-h-[44px] font-mono text-xs"
            placeholder="https://api.example.com/gateway/submit"
          />
          <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
            <input v-model="newGateway.append_submit_php" type="checkbox" />
            自动补 /submit.php（易支付标准布局）
          </label>
          <p class="break-all text-xs text-ink-3">
            实际提交到：{{ resolvePreview(newGateway.submit_url, newGateway.append_submit_php) || '—' }}
          </p>
        </div>

        <div>
          <label class="label" for="new-gateway-secret">商户密钥</label>
          <input
            id="new-gateway-secret"
            v-model="newGateway.secret_key"
            type="password"
            autocomplete="new-password"
            class="input min-h-[44px]"
            placeholder="请输入商户密钥"
          />
        </div>
        <div>
          <label class="label" for="new-gateway-query">查询地址（可选）</label>
          <input
            id="new-gateway-query"
            v-model="newGateway.query_url"
            type="url"
            inputmode="url"
            class="input min-h-[44px] font-mono text-xs"
            placeholder="可留空，易支付布局自动使用 api.php"
          />
        </div>

        <div class="sm:col-span-2">
          <label class="label">支付方式</label>
          <div class="flex flex-wrap items-center gap-x-5">
            <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
              <input v-model="newGateway.alipay_enabled" type="checkbox" />
              支付宝
            </label>
            <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
              <input v-model="newGateway.wxpay_enabled" type="checkbox" />
              微信
            </label>
            <label class="flex min-h-[44px] cursor-pointer items-center gap-2 text-sm text-ink-1">
              <input v-model="newGateway.enabled" type="checkbox" />
              启用该网关
            </label>
          </div>
        </div>
      </div>

      <div class="mt-4">
        <button
          type="submit"
          class="btn-primary min-h-[44px] !px-5"
          :disabled="Boolean(busyKey) || savingSettings"
        >{{ busyKey === 'create' ? '创建中...' : '新增网关' }}</button>
      </div>
    </form>
  </section>
</template>
