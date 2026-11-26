<script setup>
import { computed } from 'vue'

import { formatCount } from '@/utils/display'
import { buildPageItems } from '@/utils/pagination'

const props = defineProps({
  page: {
    type: Number,
    default: 1,
  },
  pageCount: {
    type: Number,
    default: 1,
  },
  total: {
    type: Number,
    default: null,
  },
})

const emit = defineEmits(['change'])

const normalizedPageCount = computed(() => {
  const numeric = Number(props.pageCount)
  if (!Number.isFinite(numeric)) return 1
  return Math.max(1, Math.trunc(numeric))
})

const normalizedPage = computed(() => {
  const numeric = Number(props.page)
  if (!Number.isFinite(numeric)) return 1
  return Math.min(normalizedPageCount.value, Math.max(1, Math.trunc(numeric)))
})

const pageItems = computed(() => buildPageItems(
  normalizedPage.value,
  normalizedPageCount.value,
))

const showTotal = computed(() => {
  if (props.total === null || props.total === undefined) return false
  return Number.isFinite(Number(props.total))
})

function goTo(target) {
  if (target < 1 || target > normalizedPageCount.value) return
  if (target === normalizedPage.value) return
  emit('change', target)
}
</script>

<template>
  <nav
    v-if="normalizedPageCount > 1"
    class="flex w-full min-w-0 flex-wrap items-center justify-center gap-1.5 py-4"
    aria-label="分页"
  >
    <p
      v-if="showTotal"
      class="w-full text-center text-xs text-ink-3"
    >
      共 {{ formatCount(total) }} 条 · 第 {{ normalizedPage }} / {{ normalizedPageCount }} 页
    </p>

    <button
      type="button"
      class="btn-secondary !px-3 !py-2"
      :disabled="normalizedPage <= 1"
      aria-label="上一页"
      @click="goTo(normalizedPage - 1)"
    >
      上一页
    </button>

    <template v-for="(item, index) in pageItems" :key="index">
      <span
        v-if="item === 'ellipsis'"
        class="px-1 text-sm text-ink-3"
        aria-hidden="true"
      >
        …
      </span>
      <button
        v-else
        type="button"
        :class="item === normalizedPage ? 'filter-pill-active' : 'filter-pill'"
        :aria-current="item === normalizedPage ? 'page' : undefined"
        :aria-label="`第 ${item} 页`"
        @click="goTo(item)"
      >
        {{ item }}
      </button>
    </template>

    <button
      type="button"
      class="btn-secondary !px-3 !py-2"
      :disabled="normalizedPage >= normalizedPageCount"
      aria-label="下一页"
      @click="goTo(normalizedPage + 1)"
    >
      下一页
    </button>
  </nav>
</template>
