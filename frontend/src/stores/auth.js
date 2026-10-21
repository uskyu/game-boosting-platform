/**
 * Authentication store using Pinia.
 * Manages user authentication state, tokens, and auth operations.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api, { refreshAccessToken } from '@/utils/api'
import { beginSessionRotation, resetSessionRotation } from '@/utils/sessionRotation'

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
  // 最近一次退出的原因（null = 用户主动登出）。被服务端踢下线时置
  // 'session-expired'，让登录页能解释"为什么突然回到这里"，而不是让用户
  // 一脸茫然地重新输入密码。
  const lastLogoutReason = ref(null)

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
        // 注意：这里不能用 token 是否变化来判断 stale——401 拦截器刷新令牌是
        // 请求成功路径的常态（冷加载 access 过期时），刷新后 token 必然变化，
        // 但响应就是同一个账号的有效会话；把这种结果当 stale 丢弃会导致
        // "带着新令牌却永远未登录"。账号切换/登出由 sessionGeneration 覆盖。
        if (requestSeq !== authRequestSeq || generation !== sessionGeneration.value) {
          return { success: false, stale: true }
        }
        setUser(response.data)
        return { success: true }
      } catch (err) {
        // Token might be invalid, clear everything, but only for this session.
        if (requestSeq !== authRequestSeq || generation !== sessionGeneration.value) {
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

  // 改密码会作废旧令牌并返回新令牌。在这段"令牌正在轮换"的窗口里，其他
  // 在飞请求可能带着旧令牌拿到 401 —— 此时绝不能走刷新（旧 refresh 已被
  // 服务端版本号作废，刷新必失败 → 把操作者自己当成被盗号踢下线）。闸门
  // 让拦截器先等轮换完成，再用新令牌重放。
  async function changePassword(currentPassword, newPassword) {
    loading.value = true
    error.value = null
    const endRotation = beginSessionRotation()

    try {
      const response = await api.post(
        '/auth/change-password',
        {
          current_password: currentPassword,
          new_password: newPassword,
        },
        // 本请求自己不能等闸门，否则会等自己形成死锁
        { skipSessionRotationWait: true },
      )
      const { access_token, refresh_token, user: userData } = response.data
      if (access_token && refresh_token) {
        setTokens(access_token, refresh_token)
      }
      if (userData) {
        setUser(userData)
      }
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      endRotation()
      loading.value = false
    }
  }

  function logout(reason = null) {
    authRequestSeq += 1
    meInFlight = null
    meInFlightToken = null
    meInFlightGeneration = null
    sessionGeneration.value += 1
    resetSessionRotation()
    lastLogoutReason.value = reason
    clearTokens()
    user.value = null
    error.value = null
    // 注销会作废在飞的 fetchCurrentUser：其 finally 因序号不匹配跳过复位，
    // 这里必须兜底清掉 loading，否则登录页的按钮会被永久禁用（"一直点没反应"），
    // 只有整页刷新才能恢复。
    loading.value = false
  }

  function clearLogoutReason() {
    lastLogoutReason.value = null
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
      return
    }
    // 只有 refresh token（access 过期/丢失）也要恢复会话，否则带着有效
    // 登录凭证打开页面会停在未登录态。刷新失败再清空。
    if (refreshToken.value) {
      try {
        await refreshAccessToken({ refreshToken: refreshToken.value, setTokens })
        await fetchCurrentUser()
      } catch {
        clearTokens()
      }
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
    lastLogoutReason,
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
    clearLogoutReason,
    getSessionContext,
    isCurrentSession,
    initialize,
  }
})
