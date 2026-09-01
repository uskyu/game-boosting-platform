<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settings'

/**
 * 主题切换按钮：light → dark → system 三态循环（文档 2.2）。
 * 图标为内联 SVG（太阳 / 月亮 / 自动-显示器）。
 */
const settingsStore = useSettingsStore()

const ORDER = ['light', 'dark', 'system']

const LABELS = {
  light: '亮色',
  dark: '暗色',
  system: '跟随系统',
}

const current = computed(() => settingsStore.theme)
const resolved = computed(() => settingsStore.resolvedTheme)

const nextTheme = computed(() => {
  const index = ORDER.indexOf(current.value)
  return ORDER[(index + 1) % ORDER.length]
})

const title = computed(() => `主题：${LABELS[current.value]}，点击切换到${LABELS[nextTheme.value]}`)
const ariaLabel = computed(() => `切换主题（当前${LABELS[current.value]}）`)

function cycle() {
  settingsStore.setTheme(nextTheme.value)
}
</script>

<template>
  <button
    type="button"
    class="inline-flex h-10 w-10 items-center justify-center rounded-full text-ink-2 transition-colors duration-base hover:bg-surface-3 hover:text-ink-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
    :title="title"
    :aria-label="ariaLabel"
    data-theme-toggle
    @click="cycle"
  >
    <!-- 亮色：太阳 -->
    <svg
      v-if="current === 'light'"
      class="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>

    <!-- 暗色：月亮 -->
    <svg
      v-else-if="current === 'dark'"
      class="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M21 12.8A8.6 8.6 0 1 1 11.2 3a7.2 7.2 0 0 0 9.8 9.8Z" />
    </svg>

    <!-- 跟随系统：显示器 -->
    <svg
      v-else
      class="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <rect x="2.5" y="4" width="19" height="13" rx="2.5" />
      <path d="M8.5 20.5h7M12 17v3.5" />
      <path d="M7.5 10.5a4.5 4.5 0 0 1 4.5-4.5" />
    </svg>

    <span class="sr-only">{{ LABELS[current] }}（当前实际{{ resolved === 'dark' ? '暗色' : '亮色' }}）</span>
  </button>
</template>
