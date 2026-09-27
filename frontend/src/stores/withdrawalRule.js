import { defineStore } from 'pinia'

import api from '@/utils/api'

/**
 * 后台提现刷新规则设置。
 * INTERVAL：每个用户自上次提现起每隔 N 小时恢复 1 次机会；
 * DAILY_NOON：全站每天 12:00 统一刷新。改规则即时生效，
 * 正在等待中的提现会按新规则重新计算机会。
 */
export const useWithdrawalRuleStore = defineStore('withdrawalRule', {
  state: () => ({
    settings: null,
    loading: false,
  }),
  actions: {
    async fetchSettings(force = false) {
      if (this.settings && !force) {
        return { success: true, data: this.settings }
      }
      this.loading = true
      try {
        const response = await api.get('/admin/withdrawal-rule/settings')
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
        const response = await api.put('/admin/withdrawal-rule/settings', payload)
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
