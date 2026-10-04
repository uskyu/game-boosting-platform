import { defineStore } from 'pinia'

import api from '@/utils/api'

/**
 * 全局服务费设置（后台）。
 * 逐单设置关闭时，新订单统一快照后台全局费率；
 * 开启后可在订单发布时单独关闭或覆盖费率。
 */
export const useServiceFeeStore = defineStore('serviceFee', {
  state: () => ({
    settings: null,
    loading: false,
  }),
  getters: {
    rate: (state) => Number(state.settings?.service_fee_rate ?? 0),
    individualEnabled: (state) => Boolean(state.settings?.individual_service_fee_enabled),
  },
  actions: {
    async fetchSettings(force = false) {
      if (this.settings && !force) {
        return { success: true, data: this.settings }
      }
      this.loading = true
      try {
        const response = await api.get('/admin/service-fee/settings')
        this.settings = response.data
        return { success: true, data: response.data }
      } catch (err) {
        return { success: false, error: err.message }
      } finally {
        this.loading = false
      }
    },
    async updateSettings(payload) {
      this.loading = true
      try {
        const response = await api.put('/admin/service-fee/settings', payload)
        this.settings = response.data
        return { success: true, data: response.data }
      } catch (err) {
        return { success: false, error: err.message }
      } finally {
        this.loading = false
      }
    },
  },
})
