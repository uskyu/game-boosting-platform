import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import api from '@/utils/api'

function buildPagination(page = 1, pageSize = 20) {
  return {
    items: [],
    total: 0,
    page,
    page_size: pageSize,
    pages: 0,
  }
}

function normalizeQueryParams(params) {
  const normalized = { ...params }

  Object.keys(normalized).forEach((key) => {
    if (
      normalized[key] === undefined ||
      normalized[key] === null ||
      normalized[key] === ''
    ) {
      delete normalized[key]
    }
  })

  return normalized
}

export const useSearchStore = defineStore('search', () => {
  const loading = ref(false)
  const error = ref(null)
  const filters = ref({
    q: '',
    type: 'all',
    game_id: '',
    category: '',
    platform: '',
    price_min: '',
    price_max: '',
    service_type: '',
    page: 1,
    page_size: 20,
  })
  const ordersResult = ref(buildPagination())
  const servicesResult = ref(buildPagination())
  const lastResponse = ref(null)

  const activeTab = computed(() => {
    if (filters.value.type === 'services') {
      return 'services'
    }
    return 'orders'
  })

  const hasResults = computed(() => {
    return (ordersResult.value.total || 0) > 0 || (servicesResult.value.total || 0) > 0
  })

  async function search(params = {}) {
    loading.value = true
    error.value = null

    filters.value = {
      ...filters.value,
      ...params,
    }

    const requestParams = normalizeQueryParams(filters.value)

    try {
      const response = await api.get('/search/', { params: requestParams })
      lastResponse.value = response.data
      ordersResult.value = response.data.orders || buildPagination(filters.value.page, filters.value.page_size)
      servicesResult.value = response.data.services || buildPagination(filters.value.page, filters.value.page_size)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      ordersResult.value = buildPagination(filters.value.page, filters.value.page_size)
      servicesResult.value = buildPagination(filters.value.page, filters.value.page_size)
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  function setFilters(nextFilters = {}) {
    filters.value = {
      ...filters.value,
      ...nextFilters,
    }
  }

  function setPage(page) {
    filters.value.page = page
  }

  function reset() {
    filters.value = {
      q: '',
      type: 'all',
      game_id: '',
      category: '',
      platform: '',
      price_min: '',
      price_max: '',
      service_type: '',
      page: 1,
      page_size: 20,
    }
    ordersResult.value = buildPagination()
    servicesResult.value = buildPagination()
    lastResponse.value = null
    error.value = null
  }

  return {
    loading,
    error,
    filters,
    ordersResult,
    servicesResult,
    lastResponse,
    activeTab,
    hasResults,
    search,
    setFilters,
    setPage,
    reset,
  }
})
