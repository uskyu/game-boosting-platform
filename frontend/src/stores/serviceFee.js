import { defineStore } from 'pinia'

import api from '@/utils/api'

/**
 * 全局服务费设置（后台）。
 * 管理员发布订单开启「服务费」但未手输费率时，按发布瞬间的全局费率收取；
 * 已发布订单不受影响。
 */
export const useServiceFeeStore = defineStore('serviceFee', {
  state: () => ({
    settings: null,
    loading: false,
  }),
  getters: {
    rate: (state) => Number(state.settings?.service_fee_rate ?? 0),
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
