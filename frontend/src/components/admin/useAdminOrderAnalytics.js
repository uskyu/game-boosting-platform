import { ref } from 'vue'

import api from '@/utils/api'

const PERIODS = new Set(['today', '7d', '30d'])
const PAGE_SIZE = 20

function errorMessage(error, fallback) {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : (error?.message || fallback)
}

/**
 * Fetches an order-analytics summary and its drill-down rows.
 * Period boundaries and metric definitions are supplied by the API in Beijing time.
 */
function useOrderAnalytics(endpoint, apiClient) {
  const period = ref('today')
  const summary = ref(null)
  const daily = ref([])
  const lifetime = ref(null)
  const loading = ref(false)
  const error = ref('')

  const selectedDate = ref('')
  const selectedMetric = ref('')
  const details = ref([])
  const detailsLoading = ref(false)
  const detailsError = ref('')
  const detailsPage = ref(1)
  const detailsPages = ref(1)
  const detailsTotal = ref(0)

  let summaryRequestId = 0
  let detailsRequestId = 0

  async function loadSummary() {
    const requestId = ++summaryRequestId
    const requestedPeriod = period.value
    loading.value = true
    error.value = ''
    summary.value = null
    daily.value = []
    try {
      const response = await apiClient.get(endpoint, {
        params: { period: requestedPeriod },
      })
      if (requestId !== summaryRequestId || requestedPeriod !== period.value) return
      const data = response.data || {}
      summary.value = data.summary || data
      daily.value = Array.isArray(data.daily) ? data.daily : []
      lifetime.value = data.lifetime || null
    } catch (requestError) {
      if (requestId === summaryRequestId) {
        error.value = errorMessage(requestError, '发单数据加载失败，请稍后重试')
      }
    } finally {
      if (requestId === summaryRequestId) loading.value = false
    }
  }

  async function setPeriod(nextPeriod) {
    if (!PERIODS.has(nextPeriod) || nextPeriod === period.value) return
    period.value = nextPeriod
    detailsRequestId += 1
    selectedDate.value = ''
    selectedMetric.value = ''
    details.value = []
    detailsError.value = ''
    detailsPage.value = 1
    detailsPages.value = 1
    detailsTotal.value = 0
    await loadSummary()
  }

  async function fetchDetailsPage(date, metric, page, { append = false } = {}) {
    const requestId = ++detailsRequestId
    const requestedPeriod = period.value
    detailsLoading.value = true
    detailsError.value = ''
    try {
      const response = await apiClient.get(`${endpoint}/details`, {
        params: {
          period: requestedPeriod,
          date,
          metric,
          page,
          page_size: PAGE_SIZE,
        },
      })
      if (requestId !== detailsRequestId || requestedPeriod !== period.value) return
      const data = response.data || {}
      const items = Array.isArray(data.items) ? data.items : []
      details.value = append ? [...details.value, ...items] : items
      detailsPage.value = Number(data.page || page)
      detailsPages.value = Math.max(1, Number(data.pages || 1))
      detailsTotal.value = Number(data.total ?? details.value.length)
    } catch (requestError) {
      if (requestId === detailsRequestId) {
        detailsError.value = errorMessage(requestError, '订单明细加载失败，请稍后重试')
      }
    } finally {
      if (requestId === detailsRequestId) detailsLoading.value = false
    }
  }

  async function openDetails(date, metric) {
    selectedDate.value = date
    selectedMetric.value = metric
    details.value = []
    detailsPage.value = 1
    detailsPages.value = 1
    detailsTotal.value = 0
    await fetchDetailsPage(date, metric, 1)
  }

  async function loadMoreDetails() {
    if (detailsLoading.value || detailsPage.value >= detailsPages.value) return
    await fetchDetailsPage(selectedDate.value, selectedMetric.value, detailsPage.value + 1, { append: true })
  }

  function closeDetails() {
    detailsRequestId += 1
    selectedDate.value = ''
    selectedMetric.value = ''
    details.value = []
    detailsLoading.value = false
    detailsError.value = ''
    detailsPage.value = 1
    detailsPages.value = 1
    detailsTotal.value = 0
  }

  return {
    period,
    summary,
    daily,
    lifetime,
    loading,
    error,
    selectedDate,
    selectedMetric,
    details,
    detailsLoading,
    detailsError,
    detailsPage,
    detailsPages,
    detailsTotal,
    loadSummary,
    setPeriod,
    openDetails,
    loadMoreDetails,
    closeDetails,
  }
}

/** Platform-wide admin analytics. */
export function useAdminOrderAnalytics(apiClient = api) {
  return useOrderAnalytics('/admin/order-analytics', apiClient)
}

/** Current user's analytics; the backend scopes results to the authenticated account. */
export function useMyOrderAnalytics(apiClient = api) {
  return useOrderAnalytics('/orders/my-analytics', apiClient)
}

