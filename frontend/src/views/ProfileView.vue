<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useWalletStore } from '@/stores/wallet'
import api from '@/utils/api'
import { formatDate, formatPrice } from '@/utils/display'
import { getApplicationStatusMeta, getUserRoleMeta } from '@/utils/order'

const router = useRouter()

const authStore = useAuthStore()
const walletStore = useWalletStore()

const user = computed(() => authStore.user)
const roleMeta = computed(() => getUserRoleMeta(user.value?.role))
const applicationMeta = computed(() => getApplicationStatusMeta(application.value?.status || 'NONE'))
const avatarText = computed(() => user.value?.username?.slice(0, 1)?.toUpperCase() || 'U')
const proofFileName = computed(() => appForm.value.proof_image?.name || '未选择')
function isPasswordStrong(pw) {
  return pw.length >= 8 && /[A-Z]/.test(pw) && /\d/.test(pw)
}

const canSubmitPassword = computed(() => {
  return (
    passwordForm.value.currentPassword.trim() !== '' &&
    isPasswordStrong(passwordForm.value.newPassword) &&
    passwordForm.value.newPassword === passwordForm.value.confirmPassword
  )
})

const shouldShowApplicationForm = computed(() => {
  if (user.value?.role === 'ADMIN') {
    return false
  }
  return !application.value || ['NONE', 'REJECTED'].includes(application.value.status)
})

const profileForm = ref({ username: '', phone: '', bio: '' })
const passwordForm = ref({ currentPassword: '', newPassword: '', confirmPassword: '' })
const appForm = ref({ game_name: '', current_rank: '', target_rank: '', note: '', proof_image: null })
const application = ref(null)
const profileMessage = ref({ type: '', text: '' })
const passwordMessage = ref({ type: '', text: '' })
const applicationMessage = ref({ type: '', text: '' })
const savingProfile = ref(false)
const changingPassword = ref(false)
const submittingApplication = ref(false)

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

function resetProfileForm() {
  profileForm.value = {
    username: user.value?.username || '',
    phone: user.value?.phone || '',
    bio: user.value?.bio || '',
  }
}

function hydrateApplicationForm() {
  appForm.value.game_name = application.value?.game_name || ''
  appForm.value.current_rank = application.value?.current_rank || ''
  appForm.value.target_rank = application.value?.target_rank || ''
  appForm.value.note = application.value?.note || ''
  appForm.value.proof_image = null
}

async function fetchApplication() {
  try {
    const res = await api.get('/users/me/booster-application')
    application.value = res.data
    hydrateApplicationForm()
  } catch {
    application.value = null
  }
}

async function updateProfile() {
  profileMessage.value = { type: '', text: '' }
  savingProfile.value = true
  const result = await authStore.updateProfile(profileForm.value)
  profileMessage.value = result.success
    ? { type: 'success', text: '已保存' }
    : { type: 'error', text: result.error }
  savingProfile.value = false
}

async function handleLogout() {
  authStore.logout()
  router.push({ name: 'login' })
}

async function changePassword() {
  passwordMessage.value = { type: '', text: '' }
  if (!canSubmitPassword.value) {
    passwordMessage.value = { type: 'error', text: '请检查密码' }
    return
  }

  changingPassword.value = true
  const result = await authStore.changePassword(passwordForm.value.currentPassword, passwordForm.value.newPassword)
  if (result.success) {
    passwordMessage.value = { type: 'success', text: '密码已更新' }
    passwordForm.value = { currentPassword: '', newPassword: '', confirmPassword: '' }
  } else {
    passwordMessage.value = { type: 'error', text: result.error }
  }
  changingPassword.value = false
}

function onProofChange(event) {
  appForm.value.proof_image = event.target.files?.[0] || null
}

async function submitBoosterApplication() {
  applicationMessage.value = { type: '', text: '' }
  if (!appForm.value.proof_image) {
    applicationMessage.value = { type: 'error', text: '请上传截图' }
    return
  }

  submittingApplication.value = true
  try {
    const form = new FormData()
    form.append('game_name', appForm.value.game_name)
    form.append('current_rank', appForm.value.current_rank)
    form.append('target_rank', appForm.value.target_rank)
    if (appForm.value.note) form.append('note', appForm.value.note)
    form.append('proof_image', appForm.value.proof_image)
    const res = await api.post('/users/booster-application', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    application.value = res.data
    hydrateApplicationForm()
    applicationMessage.value = { type: 'success', text: '申请已提交' }
  } catch (error) {
    applicationMessage.value = { type: 'error', text: error.message || '提交失败' }
  }
  submittingApplication.value = false
}

onMounted(async () => {
  resetProfileForm()
  // 可用余额仅作展示，获取失败静默跳过
  walletStore.fetchWallet()
  await fetchApplication()
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="hero-panel p-6 sm:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
        <div class="space-y-4">
          <div class="flex items-center gap-5">
            <div class="relative">
              <div class="relative flex h-16 w-16 items-center justify-center rounded-[22px] border border-line-1 bg-primary-soft text-2xl font-semibold text-primary">
                {{ avatarText }}
              </div>
            </div>
            <div class="space-y-2">
              <div class="flex flex-wrap items-center gap-3">
                <p class="eyebrow">我的</p>
                <span :class="roleMeta.badgeClass">{{ roleMeta.label }}</span>
              </div>
              <h1 class="section-title">{{ user?.username || '个人中心' }}</h1>
              <p class="text-sm text-ink-2">{{ user?.email }}</p>
            </div>
          </div>

          <!-- “我的”页快捷入口：我的订单 / 陪玩服务 / 钱包 / 设置 / 退出登录 -->
          <div class="flex flex-wrap items-center gap-2">
            <router-link to="/orders" class="btn-secondary !px-4">我的订单</router-link>
            <router-link v-if="user?.role !== 'ADMIN'" to="/services" class="btn-secondary !px-4">
              {{ user?.role === 'BOOSTER' ? '接单工作台' : '陪玩市场' }}
            </router-link>
            <router-link to="/wallet" class="btn-secondary !px-4">钱包</router-link>
            <router-link to="/settings" class="btn-secondary !px-4">设置</router-link>
            <button type="button" class="btn-ghost !px-4" @click="handleLogout">退出登录</button>
          </div>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <article class="stat-card xl:col-span-1">
            <p class="text-xs font-medium uppercase tracking-[0.12em] text-ink-2">可用余额</p>
            <p class="mt-2.5 text-2xl font-semibold tabular-nums text-ink-1">{{ formatPrice(walletStore.wallet?.available_balance ?? 0) }}</p>
          </article>
          <article class="stat-card"><p class="text-sm text-ink-2">注册</p><p class="mt-2 text-lg font-semibold text-ink-1">{{ formatDate(user?.created_at) }}</p></article>
          <article class="stat-card"><p class="text-sm text-ink-2">账号</p><p class="mt-2 text-lg font-semibold text-ink-1">{{ user?.is_active ? '正常' : '停用' }}</p></article>
          <article class="stat-card"><p class="text-sm text-ink-2">申请</p><p class="mt-2 text-lg font-semibold text-ink-1">{{ applicationMeta.label }}</p></article>
          <article v-if="user?.role === 'BOOSTER' && application" class="stat-card sm:col-span-2 xl:col-span-4"><p class="text-sm text-ink-2">接单额度</p><p class="mt-2 text-lg font-semibold text-ink-1">{{ application.booster_quota ?? 0 }} 单</p></article>
        </div>
      </div>
    </section>

    <div class="grid gap-6 xl:grid-cols-[1.02fr_0.98fr]">
      <section class="space-y-6">
        <article class="surface-card p-6 sm:p-8">
          <div class="flex items-center justify-between gap-4">
            <h2 class="text-2xl font-semibold text-ink-1">资料</h2>
            <span class="text-sm text-ink-3">编辑</span>
          </div>

          <div v-if="profileMessage.text" class="mt-4" :class="messageClass(profileMessage.type)">{{ profileMessage.text }}</div>

          <form class="mt-6 space-y-5" @submit.prevent="updateProfile">
            <div class="grid gap-5 sm:grid-cols-2">
              <div>
                <label class="label" for="profile-username">昵称</label>
                <input id="profile-username" v-model="profileForm.username" type="text" class="input" />
              </div>
              <div>
                <label class="label" for="profile-phone">手机号</label>
                <input id="profile-phone" v-model="profileForm.phone" type="text" class="input" />
              </div>
            </div>
            <div>
              <label class="label" for="profile-bio">简介</label>
              <textarea id="profile-bio" v-model="profileForm.bio" rows="4" class="input resize-none"></textarea>
            </div>
            <button class="btn-primary w-full py-3" :disabled="savingProfile">{{ savingProfile ? '保存中...' : '保存' }}</button>
          </form>
        </article>

        <article class="surface-card p-6 sm:p-8">
          <div class="flex items-center justify-between gap-4">
            <h2 class="text-2xl font-semibold text-ink-1">密码</h2>
            <span class="text-sm text-ink-3">安全</span>
          </div>

          <div v-if="passwordMessage.text" class="mt-4" :class="messageClass(passwordMessage.type)">{{ passwordMessage.text }}</div>

          <form class="mt-6 space-y-5" @submit.prevent="changePassword">
            <div>
              <label class="label" for="current-password">当前密码</label>
              <input id="current-password" v-model="passwordForm.currentPassword" type="password" class="input" />
            </div>
            <div class="grid gap-5 sm:grid-cols-2">
              <div>
                <label class="label" for="new-password">新密码</label>
                <input id="new-password" v-model="passwordForm.newPassword" type="password" class="input" />
              </div>
              <div>
                <label class="label" for="confirm-new-password">确认</label>
                <input id="confirm-new-password" v-model="passwordForm.confirmPassword" type="password" class="input" />
              </div>
            </div>
            <button class="btn-secondary w-full py-3" :disabled="changingPassword || !canSubmitPassword">{{ changingPassword ? '提交中...' : '更新密码' }}</button>
          </form>
        </article>
      </section>

      <aside class="surface-card p-6 sm:p-8">
        <div class="flex items-center justify-between gap-4">
          <h2 class="text-2xl font-semibold text-ink-1">代练申请</h2>
          <span :class="applicationMeta.badgeClass">{{ applicationMeta.label }}</span>
        </div>

        <div v-if="applicationMessage.text" class="mt-4" :class="messageClass(applicationMessage.type)">{{ applicationMessage.text }}</div>

        <div class="mt-6 grid gap-4">
          <div class="info-tile">
            <p class="info-tile__label">状态</p>
            <p class="info-tile__value">{{ applicationMeta.description }}</p>
          </div>
          <div v-if="application?.review_note" class="message-warning p-4">
            <p class="info-tile__label">备注</p>
            <p class="mt-2 text-sm font-medium">{{ application.review_note }}</p>
          </div>
        </div>

        <form v-if="shouldShowApplicationForm" class="mt-6 space-y-5" @submit.prevent="submitBoosterApplication">
          <div>
            <label class="label" for="apply-game">游戏</label>
            <input id="apply-game" v-model="appForm.game_name" type="text" class="input" required />
          </div>
          <div class="grid gap-5 sm:grid-cols-2">
            <div>
              <label class="label" for="apply-current-rank">当前段位</label>
              <input id="apply-current-rank" v-model="appForm.current_rank" type="text" class="input" required />
            </div>
            <div>
              <label class="label" for="apply-target-rank">擅长目标</label>
              <input id="apply-target-rank" v-model="appForm.target_rank" type="text" class="input" required />
            </div>
          </div>
          <div>
            <label class="label" for="apply-note">补充</label>
            <textarea id="apply-note" v-model="appForm.note" rows="4" class="input resize-none"></textarea>
          </div>
          <div class="rounded-tile border border-dashed border-line-2 bg-surface-2 p-4 transition-colors duration-base hover:border-line-2">
            <label class="label" for="apply-proof">截图</label>
            <input id="apply-proof" type="file" accept="image/*" class="input" @change="onProofChange" />
            <p class="helper-text">{{ proofFileName }}</p>
          </div>
          <button class="btn-success w-full py-3" :disabled="submittingApplication">{{ submittingApplication ? '提交中...' : '提交申请' }}</button>
        </form>

        <div v-else class="info-tile mt-6 text-sm text-ink-2">
          {{ application?.status === 'APPROVED' ? '已通过' : '审核中' }}
        </div>
      </aside>
    </div>
  </div>
</template>
