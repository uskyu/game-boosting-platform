<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AuthShell from '@/components/auth/AuthShell.vue'
import { useAuthStore } from '@/stores/auth'
import { useGamesStore } from '@/stores/games'
import { PAGE_BACKGROUNDS } from '@/data/gameImages'
import { getGamePlatformLabel, getGameServiceTypes, resolveGameVisual } from '@/utils/gameCatalog'

const router = useRouter()
const authStore = useAuthStore()
const gamesStore = useGamesStore()

const copy = {
  eyebrow: '\u521b\u5efa\u8d26\u6237',
  title: '\u6ce8\u518c\u8d26\u53f7',
  description: '',
  randomBackground: '\u5f53\u524d\u70ed\u533a',
  defaultSpotlight: '\u521b\u5efa\u4f60\u7684\u8eab\u4efd',
  defaultMeta: '\u6ce8\u518c\u540e\u5c31\u80fd\u53d1\u5e03\u9700\u6c42\u3001\u7ba1\u7406\u8ba2\u5355\u548c\u8fdb\u5165\u804a\u5929\u3002',
  createAccount: '\u521b\u5efa\u8d26\u6237',
  signUp: '\u6ce8\u518c',
  email: '\u90ae\u7bb1',
  displayName: '\u6635\u79f0',
  password: '\u5bc6\u7801',
  confirmPassword: '\u786e\u8ba4\u5bc6\u7801',
  hide: '\u9690\u85cf',
  show: '\u663e\u793a',
  emailPlaceholder: '\u8bf7\u8f93\u5165\u5e38\u7528\u90ae\u7bb1',
  displayNamePlaceholder: '\u8bf7\u8f93\u5165\u4f60\u7684\u5e73\u53f0\u6635\u79f0',
  passwordPlaceholder: '至少 8 位，含大写字母和数字',
  confirmPlaceholder: '再输入一次密码',
  passwordLength: '密码至少 8 位，须包含大写字母和数字。',
  passwordMismatch: '\u4e24\u6b21\u8f93\u5165\u7684\u5bc6\u7801\u4e0d\u4e00\u81f4\u3002',
  completeAll: '\u8bf7\u5148\u5b8c\u6210\u6240\u6709\u5fc5\u586b\u9879\u3002',
  creating: '\u521b\u5efa\u4e2d...',
  createAccountButton: '\u521b\u5efa\u8d26\u53f7',
  alreadyHaveOne: '\u5df2\u6709\u8d26\u53f7',
  backHome: '\u8fd4\u56de\u9996\u9875',
  featuredEntry: '\u8fdb\u5165\u4e13\u533a\u540e\u7ee7\u7eed\u9009\u62e9\u6a21\u5f0f',
}

const email = ref('')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const isTyping = ref(false)
const errorMessage = ref('')
const backgroundUrl = ref(PAGE_BACKGROUNDS.register)
const spotlightTitle = ref(copy.defaultSpotlight)
const spotlightMeta = ref(copy.defaultMeta)

const isLoading = computed(() => authStore.loading)

function isPasswordStrong(pw) {
  return pw.length >= 8 && /[A-Z]/.test(pw) && /\d/.test(pw)
}

const passwordError = computed(() => {
  if (password.value && !isPasswordStrong(password.value)) {
    return copy.passwordLength
  }

  if (confirmPassword.value && password.value !== confirmPassword.value) {
    return copy.passwordMismatch
  }

  return ''
})

const isFormValid = computed(() => {
  return (
    email.value.trim() !== '' &&
    username.value.trim() !== '' &&
    isPasswordStrong(password.value) &&
    password.value === confirmPassword.value
  )
})

async function handleRegister() {
  if (!isFormValid.value) {
    errorMessage.value = passwordError.value || copy.completeAll
    return
  }

  errorMessage.value = ''

  const result = await authStore.register(email.value, username.value, password.value)
  if (result.success) {
    router.push('/')
    return
  }

  errorMessage.value = result.error
}

function togglePassword() {
  showPassword.value = !showPassword.value
}

onMounted(async () => {
  await gamesStore.ensureCatalog()

  // 默认展示三角洲行动（有本地素材的旗舰游戏）；缺图阶段保留 fallback 到 randomGame。
  const featuredGame = gamesStore.catalogGames.find((game) => game.name === '三角洲行动') || gamesStore.randomGame
  const visual = resolveGameVisual(featuredGame)
  const modes = getGameServiceTypes(featuredGame).slice(0, 2).join(' / ')

  backgroundUrl.value = PAGE_BACKGROUNDS.register
  spotlightTitle.value = featuredGame?.name || copy.defaultSpotlight
  spotlightMeta.value = featuredGame
    ? `${getGamePlatformLabel(featuredGame.platform)} / ${modes || copy.featuredEntry}`
    : copy.defaultMeta
})
</script>

<template>
  <AuthShell>
    <div class="space-y-6">
      <div class="space-y-1.5">
        <p class="eyebrow">{{ copy.eyebrow }}</p>
        <h2 class="mt-2 text-2xl font-semibold text-ink-1">{{ copy.signUp }}</h2>
        <p class="text-sm text-ink-2">注册后就能发布需求、管理订单和进入聊天。</p>
      </div>

      <div v-if="errorMessage" class="message-error">
        {{ errorMessage }}
      </div>

      <form class="space-y-5" @submit.prevent="handleRegister">
        <div>
          <label for="register-email" class="label">{{ copy.email }}</label>
          <input
            id="register-email"
            v-model="email"
            type="email"
            class="input"
            :placeholder="copy.emailPlaceholder"
            autocomplete="email"
            required
            @focus="isTyping = true"
            @blur="isTyping = false"
          />
        </div>

        <div>
          <label for="register-username" class="label">{{ copy.displayName }}</label>
          <input
            id="register-username"
            v-model="username"
            type="text"
            class="input"
            :placeholder="copy.displayNamePlaceholder"
            autocomplete="username"
            required
          />
        </div>

        <div>
          <div class="mb-1.5 flex items-center justify-between">
            <label for="register-password" class="label !mb-0">{{ copy.password }}</label>
            <button type="button" class="text-xs uppercase tracking-[0.12em] text-ink-2 transition-colors hover:text-ink-1" @click="togglePassword">
              {{ showPassword ? copy.hide : copy.show }}
            </button>
          </div>
          <input
            id="register-password"
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            class="input"
            :class="{ 'input-error': password && !isPasswordStrong(password) }"
            :placeholder="copy.passwordPlaceholder"
            autocomplete="new-password"
            required
          />
        </div>

        <div>
          <label for="register-confirm-password" class="label">{{ copy.confirmPassword }}</label>
          <input
            id="register-confirm-password"
            v-model="confirmPassword"
            :type="showPassword ? 'text' : 'password'"
            class="input"
            :class="{ 'input-error': confirmPassword && password !== confirmPassword }"
            :placeholder="copy.confirmPlaceholder"
            autocomplete="new-password"
            required
          />
          <p v-if="passwordError" class="helper-text !text-danger">
            {{ passwordError }}
          </p>
        </div>

        <button
          type="submit"
          :disabled="isLoading || !isFormValid"
          class="btn-primary w-full py-3"
        >
          <svg
            v-if="isLoading"
            class="h-5 w-5 animate-spin"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
          {{ isLoading ? copy.creating : copy.createAccountButton }}
        </button>
      </form>

      <div class="cyber-divider"></div>

      <div class="grid gap-3 sm:grid-cols-2">
        <router-link to="/login" class="btn-secondary w-full py-3">
          {{ copy.alreadyHaveOne }}
        </router-link>
        <router-link to="/" class="btn-ghost w-full py-3">
          {{ copy.backHome }}
        </router-link>
      </div>
    </div>
  </AuthShell>
</template>
