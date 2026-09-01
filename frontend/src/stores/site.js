import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/utils/api'

export const useSiteStore = defineStore('site', () => {
  const settings = ref({ site_name: '游戏服务平台', site_description: '', site_logo_url: null, favicon_url: null })
  const loading = ref(false)
  const fetched = ref(false)
  async function fetchSettings() {
    if (fetched.value) return { success: true, data: settings.value }
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
