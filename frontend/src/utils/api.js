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

    const errorMessage = error.response?.data?.detail || error.message || '请求失败'

    return Promise.reject({
      status: error.response?.status,
      message: errorMessage,
      errors: error.response?.data?.errors,
      original: error,
    })
  }
)

export default api
