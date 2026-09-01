<script setup>
import { computed } from 'vue'
import { formatDateTime } from '@/utils/display'

const props = defineProps({
  order: { type: Object, required: true },
})

const isBoostOrder = computed(() => props.order?.service_type === '代练')

function fmt(v) {
  return formatDateTime(v)
}

const deadlineText = computed(() => {
  const d = props.order?.deadline
  if (!d) return ''
  const t = new Date(d)
  if (Number.isNaN(t.getTime())) return `截止 ${fmt(d)}`
  const diff = t.getTime() - Date.now()
  if (diff <= 0) return `已截止 ${fmt(d)}`
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(hours / 24)
  if (days >= 1) return `剩余 ${days}天${hours % 24}小时 · ${fmt(d)}`
  if (hours >= 1) return `剩余 ${hours}小时 · ${fmt(d)}`
  const mins = Math.max(1, Math.floor(diff / 60000))
  return `剩余 ${mins}分钟 · ${fmt(d)}`
})

const isOverdue = computed(() => {
  const d = props.order?.deadline
  if (!d) return false
  const t = new Date(d)
  return !Number.isNaN(t.getTime()) && t.getTime() <= Date.now()
})

const steps = computed(() => {
  const o = props.order
  if (!o) return []
  const s = o.status
  return [
    { key: 'created', title: '需求已发出', time: fmt(o.created_at), active: true, done: true },
    { key: 'accept', title: '确认订单', time: s === 'PENDING' ? '等待中…' : (o.locked_at ? fmt(o.locked_at) : '—'), active: ['LOCKED','DELIVERED','COMPLETED','DISPUTED'].includes(s), done: ['LOCKED','DELIVERED','COMPLETED','DISPUTED'].includes(s), pending: s === 'PENDING' },
    { key: 'locked', title: '进行中', time: o.locked_at ? fmt(o.locked_at) : '未开始', active: ['LOCKED','DELIVERED','COMPLETED','DISPUTED'].includes(s), done: ['LOCKED','DELIVERED','COMPLETED','DISPUTED'].includes(s) },
    { key: 'delivered', title: ['DELIVERED','COMPLETED'].includes(s) ? '已结束' : '待结束', time: o.delivered_at ? fmt(o.delivered_at) : '未结束', active: ['DELIVERED','COMPLETED'].includes(s), done: ['DELIVERED','COMPLETED'].includes(s) },
    { key: 'completed', title: '老板已确认', time: o.completed_at ? fmt(o.completed_at) : '待确认', active: s === 'COMPLETED', done: s === 'COMPLETED' },
  ]
})
</script>

<template>
  <section class="surface-card od-timeline-wrap p-4 sm:p-5" :class="{ 'shimmer-pending': order?.status === 'PENDING' }" aria-label="订单时间线">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-base font-semibold text-ink-1">进度</h2>
      <span v-if="deadlineText" class="od-deadline" :class="isOverdue ? 'od-deadline--overdue' : 'od-deadline--ok'">{{ deadlineText }}</span>
    </div>
    <div class="od-timeline-scroll mt-4" role="list">
      <ol class="od-timeline-track">
        <li v-for="(st, idx) in steps" :key="st.key" class="od-step" :class="{ 'od-step--active': st.active, 'od-step--pending': st.pending, 'od-step--done': st.done }" role="listitem">
          <div class="od-step__head">
            <span class="od-step__dot" aria-hidden="true">{{ idx + 1 }}</span>
            <span v-if="idx !== steps.length - 1" class="od-step__line" aria-hidden="true"></span>
          </div>
          <p class="od-step__title">{{ st.title }}</p>
          <p class="od-step__time">{{ st.time }}</p>
        </li>
      </ol>
    </div>
    <p v-if="isBoostOrder && order?.status === 'PENDING'" class="mt-3 text-xs text-ink-3">打手确认订单后开始进行</p>
  </section>
</template>
