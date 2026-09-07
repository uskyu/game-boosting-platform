<script setup>
/**
 * Reusable ECharts wrapper component.
 * Handles init, resize, dispose lifecycle — and follows the html.dark theme
 * (re-init with echarts dark/light theme when the class flips).
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

// echarts 按需动态加载：全量库约 1MB，静态引入会整个打进后台路由块，
// 弱网下拖慢后台首屏。这里只注册仪表盘用到的 bar/line/pie + 必需组件，
// 体积砍到约 1/3，并在首个图表渲染时才拉取，promise 缓存复用。
let echartsPromise = null
function loadEcharts() {
  if (!echartsPromise) {
    echartsPromise = Promise.all([
      import('echarts/core'),
      import('echarts/charts'),
      import('echarts/components'),
      import('echarts/renderers'),
    ]).then(([core, charts, components, renderers]) => {
      core.use([
        charts.BarChart,
        charts.LineChart,
        charts.PieChart,
        components.GridComponent,
        components.TooltipComponent,
        components.LegendComponent,
        renderers.CanvasRenderer,
      ])
      return core
    })
  }
  return echartsPromise
}

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '320px' },
})

const chartRef = ref(null)
let chartInstance = null
let themeObserver = null
let disposed = false

function isDark() {
  return typeof document !== 'undefined'
    && document.documentElement.classList.contains('dark')
}

async function initChart() {
  if (!chartRef.value) return
  const echarts = await loadEcharts()
  // 异步加载期间组件可能已卸载或重渲染
  if (disposed || !chartRef.value) return
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
  disposed = true
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
