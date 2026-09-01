<script setup>
/**
 * Reusable ECharts wrapper component.
 * Handles init, resize, dispose lifecycle.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '320px' },
})

const chartRef = ref(null)
let chartInstance = null

function initChart() {
  if (!chartRef.value) return
  chartInstance = echarts.init(chartRef.value, 'dark')
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
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance?.dispose()
  chartInstance = null
})
</script>

<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>
