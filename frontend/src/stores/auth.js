/**
 * Authentication store using Pinia.
 * Manages user authentication state, tokens, and auth operations.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/utils/api'

export const useAuthStore = defineStore('auth', () => {
  // State
  const user = ref(null)
  const accessToken = ref(localStorage.getItem('access_token') || null)
  const refreshToken = ref(localStorage.getItem('refresh_token') || null)
  const loading = ref(false)
  const error = ref(null)
  // Monotonic session generation invalidates in-flight requests when the
  // authenticated account changes or the current session is logged out.
  const sessionGeneration = ref(0)

  // Getters
  const isAuthenticated = computed(() => !!accessToken.value && !!user.value)
  // 平台模型：管理员（老板）发单，其余注册用户一律视为打手，可抢单/交付/提现
  const isBooster = computed(() => !!user.value && user.value.role !== 'ADMIN')
  const isAdmin = computed(() => user.value?.role === 'ADMIN')
  const userRole = computed(() => user.value?.role || null)

  // Actions
  let authRequestSeq = 0

  function setTokens(access, refresh) {
    accessToken.value = access
    refreshToken.value = refresh
    localStorage.setItem('access_token', access)
    localStorage.setItem('refresh_token', refresh)
  }

  function clearTokens() {
    accessToken.value = null
    refreshToken.value = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  function setUser(userData) {
    const previousUserId = user.value?.id ?? null
    const nextUserId = userData?.id ?? null
    if (previousUserId !== nextUserId && (previousUserId !== null || nextUserId !== null)) {
      sessionGeneration.value += 1
    }
    user.value = userData
  }

  async function fetchCaptcha() {
    try {
      const response = await api.get('/auth/captcha')
      return { success: true, captchaId: response.data.captcha_id, image: response.data.image }
    } catch (err) {
      return { success: false, error: err.message }
    }
  }

  async function register(email, username, password, captchaId, captchaCode) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.post('/auth/register', {
        email,
        username,
        password,
        captcha_id: captchaId,
        captcha_code: captchaCode,
      })
      
      const { access_token, refresh_token, user: userData } = response.data
      setTokens(access_token, refresh_token)
      setUser(userData)
      
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message, status: err.status }
    } finally {
      loading.value = false
    }
  }

  async function login(email, password) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.post('/auth/login', {
        email,
        password,
      })
      
      const { access_token, refresh_token, user: userData } = response.data
      setTokens(access_token, refresh_token)
      setUser(userData)
      
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  // 并发去重：路由守卫、生命周期、initialize 可能同时请求 /auth/me。
  // 只有同一 token/session 的请求才共享，避免账号切换时复用旧 Promise。
  let meInFlight = null
  let meInFlightToken = null
  let meInFlightGeneration = null

  async function fetchCurrentUser() {
    if (!accessToken.value) {
      return { success: false }
    }
    const generation = sessionGeneration.value
    const token = accessToken.value
    if (meInFlight && meInFlightToken === token && meInFlightGeneration === generation) {
      return meInFlight
    }
    const requestSeq = ++authRequestSeq
    meInFlightToken = token
    meInFlightGeneration = generation
    meInFlight = (async () => {
      loading.value = true
      error.value = null
      try {
        const response = await api.get('/auth/me')
        if (requestSeq !== authRequestSeq || generation !== sessionGeneration.value || token !== accessToken.value) {
          return { success: false, stale: true }
        }
        setUser(response.data)
        return { success: true }
      } catch (err) {
        // Token might be invalid, clear everything, but only for this session.
        if (requestSeq !== authRequestSeq || generation !== sessionGeneration.value || token !== accessToken.value) {
          return { success: false, stale: true, error: err.message }
        }
        if (err.status === 401) {
          logout()
        } else {
          error.value = err.message
        }
        return { success: false, error: err.message }
      } finally {
        if (requestSeq === authRequestSeq) {
          loading.value = false
          meInFlight = null
          meInFlightToken = null
          meInFlightGeneration = null
        }
      }
    })()
    return meInFlight
  }

  async function updateProfile(data) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.put('/auth/me', data)
      setUser(response.data)
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function changePassword(currentPassword, newPassword) {
    loading.value = true
    error.value = null
    
    try {
      await api.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  function logout() {
    authRequestSeq += 1
    meInFlight = null
    meInFlightToken = null
    meInFlightGeneration = null
    sessionGeneration.value += 1
    clearTokens()
    user.value = null
    error.value = null
  }

  function getSessionContext() {
    return { generation: sessionGeneration.value, userId: user.value?.id ?? null, token: accessToken.value }
  }

  function isCurrentSession(context, { requireAuthenticated = true } = {}) {
    if (!context || context.generation !== sessionGeneration.value || context.token !== accessToken.value) {
      return false
    }
    if (context.userId !== (user.value?.id ?? null)) {
      return false
    }
    return !requireAuthenticated || isAuthenticated.value
  }

  // Initialize - try to fetch user if token exists
  async function initialize() {
    if (accessToken.value) {
      await fetchCurrentUser()
    }
  }

  return {
    // State
    user,
    accessToken,
    refreshToken,
    loading,
    error,
    sessionGeneration,
    // Getters
    isAuthenticated,
    isBooster,
    isAdmin,
    userRole,
    // Actions
    setTokens,
    clearTokens,
    setUser,
    fetchCaptcha,
    register,
    login,
    fetchCurrentUser,
    updateProfile,
    changePassword,
    logout,
    getSessionContext,
    isCurrentSession,
    initialize,
  }
})
