import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/utils/api'

export const useSiteStore = defineStore('site', () => {
  // 安全设置由后端公开下发：注册/接单验证码开关与同时在线设备上限。
  // 默认值与后端 SiteSettingResponse 保持一致，避免首次请求返回前闪错。
  const settings = ref({
    site_name: '游戏服务平台',
    site_description: '',
    site_logo_url: null,
    favicon_url: null,
    registration_captcha_enabled: true,
    accept_captcha_enabled: false,
    max_online_devices: 2,
  })
  const loading = ref(false)
  const fetched = ref(false)
  // force=true 绕过已拉取缓存：站点设置可能在后台被改（验证码开关），
  // 注册页挂载时必须重新确认一次，不能沿用启动时缓存的旧值。
  async function fetchSettings(force = false) {
    if (fetched.value && !force) return { success: true, data: settings.value }
    loading.value = true
    try { const r = await api.get('/site/settings'); settings.value = { ...settings.value, ...(r.data || {}) }; fetched.value = true; return { success: true, data: settings.value } }
    catch (e) { return { success: false, error: e.message || '加载站点设置失败' } }
    finally { loading.value = false }
  }
  async function updateSettings(payload) { try { const r = await api.put('/admin/site/settings', payload); settings.value = { ...settings.value, ...r.data }; return { success: true, data: settings.value } } catch (e) { return { success: false, error: e.message || '保存失败' } } }
  async function uploadLogo(file) { const form = new FormData(); form.append('logo', file); try { const r = await api.put('/admin/site/logo', form, { headers: { 'Content-Type': 'multipart/form-data' } }); settings.value = { ...settings.value, ...r.data }; return { success: true, data: settings.value } } catch (e) { return { success: false, error: e.message || '上传失败' } } }
  async function removeLogo() { try { await api.delete('/admin/site/logo'); settings.value = { ...settings.value, site_logo_url: null, favicon_url: null }; return { success: true } } catch (e) { return { success: false, error: e.message || '删除失败' } } }
  return { settings, loading, fetchSettings, updateSettings, uploadLogo, removeLogo }
})
