import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import api from '@/utils/api'

function buildPagination(page = 1, pageSize = 20) {
  return {
    page,
    pageSize,
    total: 0,
    pages: 0,
  }
}

function normalizeParams(params) {
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

export const useServicesStore = defineStore('services', () => {
  const services = ref([])
  const myServices = ref([])
  const currentService = ref(null)
  const loading = ref(false)
  const submitting = ref(false)
  const error = ref(null)
  const pagination = ref(buildPagination())
  const myPagination = ref(buildPagination())
  const filters = ref({
    game_id: '',
    service_type: '',
    price_min: '',
    price_max: '',
    page: 1,
    page_size: 20,
  })

  const hasServices = computed(() => services.value.length > 0)
  const hasMyServices = computed(() => myServices.value.length > 0)

  async function fetchServices(options = {}) {
    loading.value = true
    error.value = null

    const merged = {
      ...filters.value,
      ...options,
    }
    filters.value = merged

    try {
      const response = await api.get('/services/', {
        params: normalizeParams(merged),
      })

      services.value = response.data.items || []
      pagination.value = {
        page: response.data.page ?? 1,
        pageSize: response.data.page_size ?? merged.page_size ?? 20,
        total: response.data.total ?? services.value.length,
        pages: response.data.pages ?? 0,
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function fetchMyServices(options = {}) {
    loading.value = true
    error.value = null

    const params = normalizeParams({
      page: options.page || myPagination.value.page,
      page_size: options.pageSize || myPagination.value.pageSize,
    })

    try {
      const response = await api.get('/services/my', { params })
      myServices.value = response.data.items || []
      myPagination.value = {
        page: response.data.page ?? 1,
        pageSize: response.data.page_size ?? params.page_size ?? 20,
        total: response.data.total ?? myServices.value.length,
        pages: response.data.pages ?? 0,
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function fetchService(id) {
    loading.value = true
    error.value = null

    try {
      const response = await api.get(`/services/${id}`)
      currentService.value = response.data

      const existingIndex = services.value.findIndex((item) => item.id === response.data.id)
      if (existingIndex !== -1) {
        services.value.splice(existingIndex, 1, response.data)
      }

      const myIndex = myServices.value.findIndex((item) => item.id === response.data.id)
      if (myIndex !== -1) {
        myServices.value.splice(myIndex, 1, response.data)
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function createService(payload) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post('/services/create', payload)
      myServices.value.unshift(response.data)
      services.value.unshift(response.data)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function updateService(id, payload) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.put(`/services/${id}`, payload)
      const nextValue = response.data
      services.value = services.value.map((item) => (item.id === id ? nextValue : item))
      myServices.value = myServices.value.map((item) => (item.id === id ? nextValue : item))
      if (currentService.value?.id === id) {
        currentService.value = nextValue
      }
      return { success: true, data: nextValue }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function deleteService(id) {
    submitting.value = true
    error.value = null

    try {
      await api.delete(`/services/${id}`)
      services.value = services.value.filter((item) => item.id !== id)
      myServices.value = myServices.value.map((item) => {
        if (item.id !== id) {
          return item
        }
        return {
          ...item,
          is_available: false,
        }
      })
      if (currentService.value?.id === id) {
        currentService.value = {
          ...currentService.value,
          is_available: false,
        }
      }
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  async function orderFromService(id, payload) {
    submitting.value = true
    error.value = null

    try {
      const response = await api.post(`/services/${id}/order`, payload)
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      submitting.value = false
    }
  }

  function setFilters(nextFilters = {}) {
    filters.value = {
      ...filters.value,
      ...nextFilters,
    }
  }

  function resetFilters() {
    filters.value = {
      game_id: '',
      service_type: '',
      price_min: '',
      price_max: '',
      page: 1,
      page_size: 20,
    }
  }

  return {
    services,
    myServices,
    currentService,
    loading,
    submitting,
    error,
    pagination,
    myPagination,
    filters,
    hasServices,
    hasMyServices,
    fetchServices,
    fetchMyServices,
    fetchService,
    createService,
    updateService,
    deleteService,
    orderFromService,
    setFilters,
    resetFilters,
  }
})
