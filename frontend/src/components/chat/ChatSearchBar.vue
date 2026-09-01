<script setup>
import { computed } from 'vue'
import { formatShortDate } from '@/utils/display'

const props = defineProps({
  modelValue: {
    type: String,
    default: '',
  },
  results: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  hasSearched: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'search', 'clear', 'select'])

const hasQuery = computed(() => props.modelValue.trim().length > 0)
const showResults = computed(() => (
  props.loading
  || props.results.length > 0
  || (props.hasSearched && hasQuery.value)
))

function updateValue(value) {
  emit('update:modelValue', value)
}

function handleSubmit() {
  emit('search')
}

function handleClear() {
  emit('update:modelValue', '')
  emit('clear')
}

function handleSelect(message) {
  emit('select', message)
}
</script>

<template>
  <div class="relative">
    <form class="chat-search-bar" @submit.prevent="handleSubmit">
      <input
        :value="modelValue"
        type="search"
        class="chat-search-input"
        placeholder="搜消息"
        @input="updateValue($event.target.value)"
      />
      <button type="submit" class="chat-search-button">
        搜索
      </button>
      <button
        v-if="hasQuery"
        type="button"
        class="chat-search-clear"
        @click="handleClear"
      >
        清空
      </button>
    </form>

    <div
      v-if="showResults"
      class="chat-search-results"
    >
      <div
        v-if="loading"
        class="px-4 py-4 text-sm text-slate-400"
      >
        搜索中...
      </div>

      <div
        v-else-if="hasSearched && hasQuery && !results.length"
        class="px-4 py-4 text-sm text-slate-500"
      >
        没有结果
      </div>

      <button
        v-for="item in results"
        :key="item.id"
        type="button"
        class="chat-search-result-item"
        @click="handleSelect(item)"
      >
        <div class="flex items-center justify-between gap-3">
          <span class="text-sm font-medium text-white">
            {{ item.sender?.username || '系统' }}
          </span>
          <span class="text-[11px] text-slate-500">
            {{ formatShortDate(item.created_at) }}
          </span>
        </div>
        <p class="chat-clamp-2 mt-2 text-sm text-slate-300">
          {{ item.content || '[图片消息]' }}
        </p>
      </button>
    </div>
  </div>
</template>
