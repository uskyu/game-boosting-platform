<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AuthShell from '@/components/auth/AuthShell.vue'
import { useAuthStore } from '@/stores/auth'
import { useGamesStore } from '@/stores/games'
import { PAGE_BACKGROUNDS } from '@/data/gameImages'
import { getGamePlatformLabel, getGameServiceTypes, resolveGameVisual } from '@/utils/gameCatalog'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const gamesStore = useGamesStore()

const copy = {
  title: '\u8d26\u6237\u5165\u53e3',
  description: '',
  accountAccess: '\u8d26\u6237\u5165\u53e3',
  randomBackground: '\u5f53\u524d\u70ed\u533a',
  currentZone: '\u5f53\u524d\u6218\u573a',
  currentZoneMeta: '\u767b\u5f55\u540e\u7ee7\u7eed\u4f60\u7684\u9700\u6c42\u3001\u8ba2\u5355\u548c\u5bf9\u8bdd\u3002',
  signIn: '\u767b\u5f55',
  pleaseEnterBoth: '\u8bf7\u586b\u5199\u90ae\u7bb1\u548c\u5bc6\u7801\u3002',
  email: '\u90ae\u7bb1',
  password: '\u5bc6\u7801',
  hide: '\u9690\u85cf',
  show: '\u663e\u793a',
  emailPlaceholder: '\u8bf7\u8f93\u5165\u5e38\u7528\u90ae\u7bb1',
  passwordPlaceholder: '\u8bf7\u8f93\u5165\u5bc6\u7801',
  entering: '\u8fdb\u5165\u4e2d...',
  enterPlatform: '\u8fdb\u5165\u5e73\u53f0',
  createAccount: '\u521b\u5efa\u8d26\u53f7',
  backHome: '\u8fd4\u56de\u9996\u9875',
  featuredEntry: '\u70ed\u95e8\u5165\u53e3',
}

const email = ref('')
const password = ref('')
const showPassword = ref(false)
const isTyping = ref(false)
const errorMessage = ref('')
const backgroundUrl = ref(PAGE_BACKGROUNDS.login)
const spotlightTitle = ref(copy.currentZone)
const spotlightMeta = ref(copy.currentZoneMeta)

const isLoading = computed(() => authStore.loading)
const isFormValid = computed(() => email.value.trim() !== '' && password.value.trim() !== '')

async function handleLogin() {
  if (!isFormValid.value) {
    errorMessage.value = copy.pleaseEnterBoth
    return
  }

  errorMessage.value = ''

  const result = await authStore.login(email.value, password.value)
  if (result.success) {
    const redirect = route.query.redirect || '/'
    router.push(redirect)
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

  backgroundUrl.value = PAGE_BACKGROUNDS.login
  spotlightTitle.value = featuredGame?.name || copy.currentZone
  spotlightMeta.value = featuredGame
    ? `${getGamePlatformLabel(featuredGame.platform)} / ${modes || copy.featuredEntry}`
    : copy.currentZoneMeta
})
</script>

<template>
  <AuthShell>
    <div class="space-y-6">
      <div class="space-y-1.5">
        <p class="eyebrow">{{ copy.accountAccess }}</p>
        <h2 class="mt-2 text-2xl font-semibold text-ink-1">{{ copy.signIn }}</h2>
        <p class="text-sm text-ink-2">登录后继续你的需求、订单和对话。</p>
      </div>

      <div v-if="errorMessage" class="message-error">
        {{ errorMessage }}
      </div>

      <form class="space-y-5" @submit.prevent="handleLogin">
        <div>
          <label for="email" class="label">{{ copy.email }}</label>
          <input
            id="email"
            v-model="email"
            type="email"
            class="input"
            :class="{ 'input-error': errorMessage && !email }"
            :placeholder="copy.emailPlaceholder"
            autocomplete="email"
            required
            @focus="isTyping = true"
            @blur="isTyping = false"
          />
        </div>

        <div>
          <div class="mb-1.5 flex items-center justify-between">
            <label for="password" class="label !mb-0">{{ copy.password }}</label>
            <button type="button" class="text-xs uppercase tracking-[0.12em] text-ink-2 transition-colors hover:text-ink-1" @click="togglePassword">
              {{ showPassword ? copy.hide : copy.show }}
            </button>
          </div>
          <input
            id="password"
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            class="input"
            :class="{ 'input-error': errorMessage && !password }"
            :placeholder="copy.passwordPlaceholder"
            autocomplete="current-password"
            required
          />
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
          {{ isLoading ? copy.entering : copy.enterPlatform }}
        </button>
      </form>

      <div class="cyber-divider"></div>

      <div class="grid gap-3 sm:grid-cols-2">
        <router-link to="/register" class="btn-secondary w-full py-3">
          {{ copy.createAccount }}
        </router-link>
        <router-link to="/" class="btn-ghost w-full py-3">
          {{ copy.backHome }}
        </router-link>
      </div>
    </div>
  </AuthShell>
</template>
