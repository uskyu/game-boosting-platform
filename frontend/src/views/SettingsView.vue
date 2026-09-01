<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const router = useRouter()
const authStore = useAuthStore()
const settingsStore = useSettingsStore()

const activeTab = ref('notifications')
const saving = ref(false)
const message = ref({ type: '', text: '' })

const tabs = [
  { key: 'notifications', label: '通知偏好', icon: '🔔' },
  { key: 'privacy', label: '隐私', icon: '🔒' },
  { key: 'cache', label: '缓存与数据', icon: '💾' },
]

const NOTIFICATION_TYPES = [
  { key: 'ORDER_ACCEPTED', label: '订单被接单', description: '有代练接受了您的订单' },
  { key: 'ORDER_DELIVERED', label: '代练提交完成', description: '代练完成了订单，等待确认' },
  { key: 'ORDER_CONFIRMED', label: '订单完成', description: '客户确认了订单完成' },
  { key: 'ORDER_DISPUTED', label: '订单争议', description: '订单被发起争议' },
  { key: 'ORDER_CANCELLED', label: '订单取消', description: '订单被取消' },
  { key: 'NEW_MESSAGE', label: '新消息', description: '收到新的聊天消息' },
  { key: 'REVIEW_RECEIVED', label: '收到评价', description: '有人对您的服务进行了评价' },
  { key: 'SYSTEM_ANNOUNCEMENT', label: '系统公告', description: '平台发布了新的公告' },
]

const localNotificationSettings = ref({})
const localPrivacySettings = ref({ profile_visible: true, show_online_status: true })

const preferences = computed(() => settingsStore.preferences)

watch(preferences, (pref) => {
  if (pref) {
    const ns = pref.notification_settings || {}
    const merged = {}
    NOTIFICATION_TYPES.forEach((t) => {
      merged[t.key] = ns[t.key] !== undefined ? ns[t.key] : true
    })
    localNotificationSettings.value = merged
    localPrivacySettings.value = {
      profile_visible: pref.profile_visible ?? true,
      show_online_status: pref.show_online_status ?? true,
    }
  }
}, { immediate: true })

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

async function saveNotificationSettings() {
  saving.value = true
  message.value = { type: '', text: '' }
  const result = await settingsStore.updatePreferences({
    notification_settings: { ...localNotificationSettings.value },
  })
  message.value = result.success
    ? { type: 'success', text: '通知偏好已保存' }
    : { type: 'error', text: result.error || '保存失败' }
  saving.value = false
}

async function savePrivacySettings() {
  saving.value = true
  message.value = { type: '', text: '' }
  const result = await settingsStore.updatePreferences({
    profile_visible: localPrivacySettings.value.profile_visible,
    show_online_status: localPrivacySettings.value.show_online_status,
  })
  message.value = result.success
    ? { type: 'success', text: '隐私设置已保存' }
    : { type: 'error', text: result.error || '保存失败' }
  saving.value = false
}

function clearSearchHistory() {
  try {
    localStorage.removeItem('search_history')
    message.value = { type: 'success', text: '搜索历史已清除' }
  } catch {
    message.value = { type: 'error', text: '清除失败' }
  }
}

function clearChatCache() {
  try {
    const keys = Object.keys(localStorage).filter((k) => k.startsWith('chat_'))
    keys.forEach((k) => localStorage.removeItem(k))
    message.value = { type: 'success', text: '聊天缓存已清除' }
  } catch {
    message.value = { type: 'error', text: '清除失败' }
  }
}

function clearAllLocalData() {
  if (!window.confirm('确定要清除所有本地缓存数据？（不会影响账号和云端数据）')) return
  try {
    const token = authStore.accessToken
    const refreshToken = authStore.refreshToken
    localStorage.clear()
    if (token) localStorage.setItem('access_token', token)
    if (refreshToken) localStorage.setItem('refresh_token', refreshToken)
    message.value = { type: 'success', text: '本地数据已清除（保留了登录状态）' }
  } catch {
    message.value = { type: 'error', text: '清除失败' }
  }
}

onMounted(async () => {
  await settingsStore.fetchPreferences()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div class="space-y-2">
          <p class="eyebrow">设置</p>
          <h1 class="section-title">偏好设置</h1>
          <p class="mt-1 text-sm text-ink-2">管理通知偏好、隐私和缓存</p>
        </div>
        <button class="btn-ghost !px-4" @click="router.push({ name: 'profile' })">
          返回个人中心
        </button>
      </div>
    </section>

    <div v-if="message.text" :class="messageClass(message.type)" class="mx-auto max-w-3xl">{{ message.text }}</div>

    <div class="mx-auto max-w-3xl space-y-6">
      <div class="flex gap-2 overflow-x-auto">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="filter-pill whitespace-nowrap"
          :class="activeTab === tab.key ? 'filter-pill-active' : ''"
          @click="activeTab = tab.key"
        >
          <span>{{ tab.icon }} {{ tab.label }}</span>
        </button>
      </div>

      <!-- Notification Settings Tab -->
      <section v-if="activeTab === 'notifications'" class="surface-card space-y-5 p-6">
        <h2 class="text-xl font-semibold text-ink-1">通知偏好</h2>
        <p class="text-sm text-ink-2">选择您希望接收的通知类型</p>

        <div class="space-y-3">
          <div
            v-for="nt in NOTIFICATION_TYPES"
            :key="nt.key"
            class="info-tile flex items-center justify-between gap-4"
          >
            <div>
              <p class="text-sm font-medium text-ink-1">{{ nt.label }}</p>
              <p class="mt-0.5 text-xs text-ink-2">{{ nt.description }}</p>
            </div>
            <label class="relative inline-flex cursor-pointer items-center">
              <input
                v-model="localNotificationSettings[nt.key]"
                type="checkbox"
                class="peer sr-only"
                :true-value="true"
                :false-value="false"
              />
              <div class="h-6 w-11 rounded-full border border-line-1 bg-surface-3 transition-colors duration-base after:absolute after:left-[3px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-knob after:transition-all after:duration-base after:content-[''] peer-checked:border-transparent peer-checked:bg-primary peer-checked:after:translate-x-full peer-checked:after:bg-on-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary"></div>
            </label>
          </div>
        </div>

        <button class="btn-primary w-full py-3" :disabled="saving" @click="saveNotificationSettings">
          {{ saving ? '保存中...' : '保存通知偏好' }}
        </button>
      </section>

      <!-- Privacy Tab -->
      <section v-if="activeTab === 'privacy'" class="surface-card space-y-5 p-6">
        <h2 class="text-xl font-semibold text-ink-1">隐私设置</h2>
        <p class="text-sm text-ink-2">控制您的个人信息可见性</p>

        <div class="space-y-3">
          <div class="info-tile flex items-center justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-ink-1">资料公开</p>
              <p class="mt-0.5 text-xs text-ink-2">其他用户可以查看您的个人资料</p>
            </div>
            <label class="relative inline-flex cursor-pointer items-center">
              <input
                v-model="localPrivacySettings.profile_visible"
                type="checkbox"
                class="peer sr-only"
                :true-value="true"
                :false-value="false"
              />
              <div class="h-6 w-11 rounded-full border border-line-1 bg-surface-3 transition-colors duration-base after:absolute after:left-[3px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-knob after:transition-all after:duration-base after:content-[''] peer-checked:border-transparent peer-checked:bg-primary peer-checked:after:translate-x-full peer-checked:after:bg-on-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary"></div>
            </label>
          </div>

          <div class="info-tile flex items-center justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-ink-1">显示在线状态</p>
              <p class="mt-0.5 text-xs text-ink-2">其他用户可以看到您是否在线</p>
            </div>
            <label class="relative inline-flex cursor-pointer items-center">
              <input
                v-model="localPrivacySettings.show_online_status"
                type="checkbox"
                class="peer sr-only"
                :true-value="true"
                :false-value="false"
              />
              <div class="h-6 w-11 rounded-full border border-line-1 bg-surface-3 transition-colors duration-base after:absolute after:left-[3px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-knob after:transition-all after:duration-base after:content-[''] peer-checked:border-transparent peer-checked:bg-primary peer-checked:after:translate-x-full peer-checked:after:bg-on-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary"></div>
            </label>
          </div>
        </div>

        <button class="btn-primary w-full py-3" :disabled="saving" @click="savePrivacySettings">
          {{ saving ? '保存中...' : '保存隐私设置' }}
        </button>
      </section>

      <!-- Cache Tab -->
      <section v-if="activeTab === 'cache'" class="surface-card space-y-5 p-6">
        <h2 class="text-xl font-semibold text-ink-1">缓存与数据</h2>
        <p class="text-sm text-ink-2">管理本地缓存数据</p>

        <div class="space-y-3">
          <div class="info-tile flex items-center justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-ink-1">搜索历史</p>
              <p class="mt-0.5 text-xs text-ink-2">清除本地存储的搜索记录</p>
            </div>
            <button class="btn-ghost !px-4 text-sm" @click="clearSearchHistory">清除</button>
          </div>

          <div class="info-tile flex items-center justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-ink-1">聊天缓存</p>
              <p class="mt-0.5 text-xs text-ink-2">清除本地缓存的聊天数据</p>
            </div>
            <button class="btn-ghost !px-4 text-sm" @click="clearChatCache">清除</button>
          </div>

          <div class="info-tile flex items-center justify-between gap-4 !bg-warning-soft">
            <div>
              <p class="text-sm font-medium text-warning">清除所有本地数据</p>
              <p class="mt-0.5 text-xs text-warning">重置所有本地缓存（保留登录状态）</p>
            </div>
            <button class="btn-ghost !px-4 text-sm !text-warning hover:!bg-warning-soft" @click="clearAllLocalData">清除全部</button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
