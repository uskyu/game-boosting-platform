<script setup>
/**
 * Reusable ECharts wrapper component.
 * Handles init, resize, dispose lifecycle — and follows the html.dark theme
 * (re-init with echarts dark/light theme when the class flips).
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '320px' },
})

const chartRef = ref(null)
let chartInstance = null
let themeObserver = null

function isDark() {
  return typeof document !== 'undefined'
    && document.documentElement.classList.contains('dark')
}

function initChart() {
  if (!chartRef.value) return
  chartInstance?.dispose()
  chartInstance = echarts.init(chartRef.value, isDark() ? 'dark' : undefined)
  chartInstance.setOption(props.option)
}

function handleResize() {
  chartInstance?.resize()
}

watch(
  () => props.option,
  (next) => {
    if (chartInstance) {
      chartInstance.setOption(next, { notMerge: true })
    }
  },
  { deep: true }
)

onMounted(() => {
  initChart()
  window.addEventListener('resize', handleResize)

  // 跟随主题切换重建图表
  if (typeof MutationObserver !== 'undefined') {
    themeObserver = new MutationObserver(() => {
      if (chartRef.value) {
        initChart()
      }
    })
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  themeObserver?.disconnect()
  themeObserver = null
  chartInstance?.dispose()
  chartInstance = null
})
</script>

<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>
