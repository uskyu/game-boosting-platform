<script setup>
/**
 * 售后处理中状态弹窗（双角色）。
 * 老板侧（viewerRole='boss'，默认）：派单卡片「售后处理中」打开，展示该单
 * 进行中售后的摘要（金额/原因全文/凭证/状态/时间），并提供「撤回售后」
 *（仅 PENDING 可撤，撤回后打手提现限制解除、该单可重新发起售后）。
 * 打手侧（viewerRole='booster'）：订单详情「售后处理中」入口打开，案情摘要
 * 不变；主按钮变「同意退款」（两步确认，同意后立即从余额扣划给老板并结案），
 * 次级按钮「联系平台协助」直达管理员会话并预填案情（与订单详情兜底入口同链路）。
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useChatStore } from '@/stores/chat'
import {
  getAfterSaleStatusLabel,
  getAfterSaleStatusTagClass,
  useAfterSalesStore,
} from '@/stores/afterSales'
import { buildAfterSaleAssistMessage } from '@/utils/afterSaleAssist'
import { formatDateTime, formatPrice } from '@/utils/display'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  orderId: { type: [Number, String], default: null },
  // 'boss' 老板侧（撤回售后）| 'booster' 打手侧（同意退款 + 联系平台协助）
  viewerRole: { type: String, default: 'boss' },
})

const emit = defineEmits(['update:modelValue', 'withdrawn', 'accepted'])

const router = useRouter()
const store = useAfterSalesStore()
const chatStore = useChatStore()

const isBoosterView = computed(() => props.viewerRole === 'booster')

const loading = ref(false)
const items = ref([])
const error = ref('')
const withdrawing = ref(false)
const confirmWithdraw = ref(false)
// 打手侧：同意退款两步确认 + 联系平台协助在飞锁
const accepting = ref(false)
const confirmAccept = ref(false)
const assistLoading = ref(false)

// 只操作进行中的那笔；没有（已被后台处理）时只展示最近一条终态
const pending = computed(() => items.value.find((item) => item.status === 'PENDING') || null)
const latest = computed(() => (items.value.length ? items.value[0] : null))
const display = computed(() => pending.value || latest.value)

function imageList(item) {
  const images = item?.images
  if (!Array.isArray(images)) return []
  return images
    .map((image) => (typeof image === 'string' ? { url: image } : image))
    .filter((image) => image?.url)
}

async function load() {
  if (props.orderId == null) return
  loading.value = true
  error.value = ''
  const result = await store.fetchOrderAfterSales(props.orderId)
  loading.value = false
  if (result.success) {
    items.value = result.data || []
  } else {
    items.value = []
    error.value = result.error || '加载售后信息失败'
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    confirmWithdraw.value = false
    confirmAccept.value = false
    load()
  }
)

async function handleWithdraw() {
  const target = pending.value
  if (!target || withdrawing.value) return
  // 两步确认：第一次点切入确认态，第二次才真撤（撤回不可恢复）
  if (!confirmWithdraw.value) {
    confirmWithdraw.value = true
    return
  }
  withdrawing.value = true
  error.value = ''
  const result = await store.withdrawAfterSale(props.orderId, target.id)
  withdrawing.value = false
  if (!result.success) {
    error.value = result.error || '撤回失败，请稍后重试'
    return
  }
  emit('withdrawn')
  emit('update:modelValue', false)
}

// 打手同意退款：与撤回同款两步确认（扣划不可恢复），成功后由父页静默刷新订单
async function handleAccept() {
  const target = pending.value
  if (!target || accepting.value) return
  if (!confirmAccept.value) {
    confirmAccept.value = true
    return
  }
  accepting.value = true
  error.value = ''
  const result = await store.acceptAfterSale(props.orderId, target.id)
  accepting.value = false
  if (!result.success) {
    error.value = result.error || '同意退款失败，请稍后重试'
    return
  }
  emit('accepted')
  emit('update:modelValue', false)
}

// 联系平台协助：与订单详情兜底入口同链路——直达管理员会话并预填售后案情，
// 会话建成后落聊天页；loading/错误处理结构照抄 OrderDetail.handleContactAdmin。
async function handleAssist() {
  if (assistLoading.value) return
  assistLoading.value = true
  error.value = ''
  try {
    const result = await chatStore.startAdminConversation({
      presetMessage: buildAfterSaleAssistMessage({
        orderId: props.orderId,
        amount: display.value?.amount,
      }),
    })
    if (result.success && result.data) {
      router.push({ name: 'chat-detail', params: { id: result.data.id } })
    } else if (result.success) {
      // stale：会话其实已在服务端创建成功，只是本地会话上下文已切换
      error.value = '会话已创建，请到消息中心查看'
    } else {
      error.value = result.error || '打开会话失败，请稍后重试'
    }
  } catch (err) {
    error.value = err?.message || '打开会话失败，请稍后重试'
  } finally {
    // 任何路径都要复位，否则按钮永久停在"打开中..."
    assistLoading.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <teleport to="body">
    <div v-if="modelValue" class="modal-scrim" @click.self="close">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="售后处理中">
        <h3 class="text-lg font-semibold text-ink-1">售后处理中</h3>

        <div v-if="loading" class="mt-4 space-y-3" aria-busy="true">
          <div class="skeleton h-5 w-2/5 !rounded-tile"></div>
          <div class="skeleton h-16 !rounded-tile"></div>
        </div>

        <template v-else-if="display">
          <p class="mt-3 text-sm text-ink-2">
            <template v-if="isBoosterView">
              老板发起了售后，平台核实处理期间该单暂不能提现；同意后将立即从你的余额扣划
              {{ formatPrice(display.amount) }} 给老板，结案后提现限制解除。
            </template>
            <template v-else>
              订单 #{{ display.order_id }} 的售后申请，平台核实处理期间该单打手暂不能提现。
            </template>
          </p>
          <dl class="mt-4 space-y-2 rounded-xl bg-surface-2 p-4 text-sm">
            <div class="flex items-center justify-between gap-3">
              <dt class="text-ink-2">售后金额</dt>
              <dd class="font-semibold tabular-nums text-price">{{ formatPrice(display.amount) }}</dd>
            </div>
            <div class="flex items-center justify-between gap-3">
              <dt class="text-ink-2">状态</dt>
              <dd>
                <span :class="['tag', getAfterSaleStatusTagClass(display.status)]">
                  {{ getAfterSaleStatusLabel(display.status) }}
                </span>
              </dd>
            </div>
            <div class="flex items-center justify-between gap-3">
              <dt class="text-ink-2">申请时间</dt>
              <dd class="tabular-nums text-ink-1">{{ formatDateTime(display.created_at) }}</dd>
            </div>
          </dl>

          <div class="mt-3">
            <p class="text-xs font-medium text-ink-2">售后原因</p>
            <p class="mt-1 whitespace-pre-wrap break-all text-sm leading-6 text-ink-1">
              {{ display.reason || '—' }}
            </p>
          </div>

          <div v-if="imageList(display).length" class="mt-3">
            <p class="text-xs font-medium text-ink-2">售后凭证</p>
            <div class="mt-1 flex flex-wrap gap-2">
              <a
                v-for="(image, index) in imageList(display)"
                :key="image.url || index"
                :href="image.url"
                target="_blank"
                rel="noopener"
                class="admin-order-thumb"
              >
                <img :src="image.url" :alt="image.name || `售后凭证 ${index + 1}`" loading="lazy" />
              </a>
            </div>
          </div>

          <div v-if="error" class="message-error mt-3">{{ error }}</div>

          <div class="mt-6 flex flex-wrap gap-3">
            <!-- 打手侧：次级按钮走平台协助兜底；无进行中售后时两边都只留关闭 -->
            <button
              v-if="isBoosterView && pending"
              type="button"
              class="btn-secondary flex-1"
              :disabled="assistLoading || accepting"
              @click="handleAssist"
            >
              {{ assistLoading ? '打开中...' : '联系平台协助' }}
            </button>
            <button v-else type="button" class="btn-secondary flex-1" @click="close">关闭</button>
            <button
              v-if="pending && isBoosterView"
              type="button"
              class="btn-danger flex-1"
              :disabled="accepting || assistLoading"
              @click="handleAccept"
            >
              {{
                accepting
                  ? '同意中...'
                  : confirmAccept
                    ? `再点一次确认扣款 ${formatPrice(pending.amount)}`
                    : `同意退款 ${formatPrice(pending.amount)}`
              }}
            </button>
            <button
              v-else-if="pending"
              type="button"
              class="btn-danger flex-1"
              :disabled="withdrawing"
              @click="handleWithdraw"
            >
              {{ withdrawing ? '撤回中...' : confirmWithdraw ? '再点一次确认撤回' : '撤回售后' }}
            </button>
          </div>
          <p v-if="pending" class="mt-2 text-xs text-ink-3">
            <template v-if="isBoosterView">
              同意退款将立即从你的余额扣划 {{ formatPrice(pending.amount) }} 给老板并结案；有异议可先联系平台协助裁定。
            </template>
            <template v-else>
              撤回后打手提现限制立即解除，该订单可重新发起售后。
            </template>
          </p>
        </template>

        <div v-else-if="error" class="message-error mt-4">{{ error }}</div>

        <div v-else class="mt-4 text-sm text-ink-2">该订单暂无售后记录。</div>
      </div>
    </div>
  </teleport>
</template>
