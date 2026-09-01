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

  // Getters
  const isAuthenticated = computed(() => !!accessToken.value && !!user.value)
  const isBooster = computed(() => user.value?.role === 'BOOSTER')
  const isAdmin = computed(() => user.value?.role === 'ADMIN')
  const userRole = computed(() => user.value?.role || null)

  // Actions
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
    user.value = userData
  }

  async function register(email, username, password) {
    loading.value = true
    error.value = null
    
    try {
      const response = await api.post('/auth/register', {
        email,
        username,
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

  async function fetchCurrentUser() {
    if (!accessToken.value) {
      return { success: false }
    }
    
    loading.value = true
    error.value = null
    
    try {
      const response = await api.get('/auth/me')
      setUser(response.data)
      return { success: true }
    } catch (err) {
      // Token might be invalid, clear everything
      if (err.status === 401) {
        logout()
      } else {
        error.value = err.message
      }
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
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
    clearTokens()
    user.value = null
    error.value = null
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
    // Getters
    isAuthenticated,
    isBooster,
    isAdmin,
    userRole,
    // Actions
    setTokens,
    clearTokens,
    setUser,
    register,
    login,
    fetchCurrentUser,
    updateProfile,
    changePassword,
    logout,
    initialize,
  }
})
