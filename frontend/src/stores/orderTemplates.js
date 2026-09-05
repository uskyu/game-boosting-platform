import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import api from '@/utils/api'
import { ORDER_TEMPLATE_FIELDS, cleanTemplatePayload } from '@/utils/orderTemplates'

export { ORDER_TEMPLATE_FIELDS }

function normalize(template) {
  return { ...template, payload: template.payload || {} }
}

export const useOrderTemplatesStore = defineStore('orderTemplates', () => {
  const templates = ref([])
  const loading = ref(false)
  const error = ref('')
  const hasTemplates = computed(() => templates.value.length > 0)

  async function fetchTemplates() {
    loading.value = true
    error.value = ''
    try {
      const response = await api.get('/order-templates')
      templates.value = (response.data || []).map(normalize)
      return { success: true, data: templates.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function createTemplate(name, fields) {
    error.value = ''
    const payload = cleanTemplatePayload(fields)
    try {
      const response = await api.post('/order-templates', { name: name.trim(), payload })
      const template = normalize(response.data)
      templates.value.unshift(template)
      return { success: true, data: template }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  async function deleteTemplate(id) {
    error.value = ''
    try {
      await api.delete(`/order-templates/${id}`)
      templates.value = templates.value.filter((item) => item.id !== id)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    }
  }

  return { templates, loading, error, hasTemplates, fetchTemplates, createTemplate, deleteTemplate }
})
