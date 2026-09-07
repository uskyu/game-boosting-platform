/**
 * Axios HTTP client configuration with JWT interceptor.
 * Automatically injects authentication token into requests.
 */

import axios from 'axios'
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

const rawApiBaseURL = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const apiBaseURL = rawApiBaseURL.endsWith('/')
  ? rawApiBaseURL.slice(0, -1)
  : rawApiBaseURL

function apiPath(path) {
  return `${apiBaseURL}${path}`
}

// Create axios instance with base configuration
const api = axios.create({
  baseURL: apiBaseURL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor - inject JWT token
api.interceptors.request.use(
  (config) => {
    const authStore = useAuthStore()
    const token = authStore.accessToken
    
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// ── Token refresh lock ──
// When multiple requests get 401 at the same time, only ONE refresh is
// issued.  The others wait for the same promise.
let refreshPromise = null

function doRefresh(authStore) {
  if (!refreshPromise) {
    refreshPromise = axios
      .post(apiPath('/auth/refresh'), {
        refresh_token: authStore.refreshToken,
      })
      .then((res) => {
        const { access_token, refresh_token } = res.data
        authStore.setTokens(access_token, refresh_token)
        return access_token
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

// ── Error message extraction ──
// 后端 422 验证失败返回 { detail, errors: [{field, message}] }，把 errors 的
// 具体原因合并成一句可读文案；其余错误沿用 detail。所有 store/页面统一受益。
function readableApiError(error) {
  const data = error.response?.data
  const messages = []

  if (Array.isArray(data?.errors)) {
    for (const item of data.errors) {
      if (typeof item === 'string') {
        messages.push(item)
      } else if (item?.message) {
        messages.push(item.message)
      }
    }
    if (messages.length > 0) return messages.join('；')
  }

  const detail = data?.detail
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (typeof item === 'string') {
        messages.push(item)
      } else if (item?.msg) {
        messages.push(item.msg)
      }
    }
    if (messages.length > 0) return messages.join('；')
  }

  return detail || error.message || '请求失败'
}

// Response interceptor - handle errors and token refresh
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    const authStore = useAuthStore()

    // Handle 401 Unauthorized
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true

      if (authStore.refreshToken) {
        try {
          const newToken = await doRefresh(authStore)
          originalRequest.headers.Authorization = `Bearer ${newToken}`
          return api(originalRequest)
        } catch {
          authStore.logout()
          router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
          return Promise.reject(error)
        }
      } else {
        authStore.logout()
        router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
      }
    }

    return Promise.reject({
      status: error.response?.status,
      message: readableApiError(error),
      errors: error.response?.data?.errors,
      original: error,
    })
  }
)

export default api
