import { defineStore } from 'pinia'

import api from '@/utils/api'

// 未拉到设置时的默认口径：支付宝/微信都开着、银行卡恒开，避免后台没配过
// 就把提现入口全禁掉；真正开关以接口返回为准。
const DEFAULT_SETTINGS = {
  alipay_enabled: true,
  wechat_enabled: true,
  updated_by: null,
  updated_at: null,
}

export const useWithdrawalSettingsStore = defineStore('withdrawalSettings', {
  state: () => ({
    settings: { ...DEFAULT_SETTINGS },
    channels: [],
    loading: false,
    // settings/channels 本身带默认值，光看它们区分不出「没拉过数据」，
    // 用这两个标记做请求缓存（与 withdrawalRule/store 的 cache 语义一致）
    settingsLoaded: false,
    channelsLoaded: false,
  }),
  getters: {
    // 渠道开关：ALIPAY/WECHAT 看后台设置，BANK 恒开；都没数据时默认开
    isChannelEnabled:
      (state) =>
      (channel) => {
        if (!channel || channel === 'BANK') {
          return true
        }
        if (state.settingsLoaded) {
          if (channel === 'ALIPAY') {
            return state.settings.alipay_enabled !== false
          }
          if (channel === 'WECHAT') {
            return state.settings.wechat_enabled !== false
          }
          return true
        }
        // 普通用户拉不到后台设置：用 /withdrawals/channels 的 enabled 兜底
        const fromChannels = state.channels.find((item) => item?.channel === channel)
        if (fromChannels) {
          return fromChannels.enabled !== false
        }
        return true
      },
  },
  actions: {
    async fetchSettings(force = false) {
      if (this.settingsLoaded && !force) {
        return { success: true, data: this.settings }
      }
      this.loading = true
      try {
        const response = await api.get('/admin/withdrawal-category/settings')
        this.settings = { ...DEFAULT_SETTINGS, ...(response.data || {}) }
        this.settingsLoaded = true
        return { success: true, data: this.settings }
      } catch (err) {
        return { success: false, error: err.message }
      } finally {
        this.loading = false
      }
    },
    async updateSettings(payload) {
      this.loading = true
      try {
        const response = await api.put('/admin/withdrawal-category/settings', payload)
        this.settings = { ...DEFAULT_SETTINGS, ...(response.data || {}) }
        this.settingsLoaded = true
        return { success: true, data: this.settings }
      } catch (err) {
        return { success: false, error: err.message }
      } finally {
        this.loading = false
      }
    },
    // GET /withdrawals/channels：面向普通用户，403（无权限）时保持 channels 为空
    async fetchChannels(force = false) {
      if (this.channelsLoaded && !force) {
        return { success: true, data: this.channels }
      }
      this.loading = true
      try {
        const response = await api.get('/withdrawals/channels')
        this.channels = Array.isArray(response.data?.items) ? response.data.items : []
        this.channelsLoaded = true
        return { success: true, data: this.channels }
      } catch (err) {
        // 拉不到渠道列表不阻塞页面：isChannelEnabled 会按默认全开兜底
        return { success: false, error: err.message }
      } finally {
        this.loading = false
      }
    },
  },
})
