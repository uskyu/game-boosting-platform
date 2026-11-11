/**
 * 发单人黑名单 store。
 *
 * 接口契约（前缀 /api/v1）：
 *   GET    /blacklist/mine?page=&page_size=  自己拉黑的人（分页）
 *   GET    /blacklist/lookup?q=              只能完全匹配用户名/登录邮箱；404 = 未找到
 *   POST   /blacklist                        { user_id, reason? }；重复拉黑幂等 200
 *   DELETE /blacklist/{blocked_user_id}      幂等 204
 *
 * 注意：后端不提供用户列表/模糊搜索接口，lookup 只返回最多一个精确命中的用户；
 * 前端不得请求或渲染全站用户列表。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import api from '@/utils/api'

// 拉黑二次确认的固定说明（快捷拉黑与黑名单页共用，避免两处文案漂移）
export const BLACKLIST_BLOCK_NOTICE = '拉黑后对方仍能看到你的订单，但不能接单；已接订单不受影响。'

export const BLACKLIST_LOOKUP_NOT_FOUND = '未找到该用户，请检查名称/账号是否完全一致'
export const BLACKLIST_LOOKUP_TOO_SHORT = '请输入至少 2 个字符的用户名或登录邮箱'

export const useBlacklistStore = defineStore('blacklist', () => {
  // 黑名单列表（GET /blacklist/mine）
  const items = ref([])
  const pagination = ref({ page: 1, pageSize: 20, total: 0, pages: 0 })
  const loading = ref(false)
  const error = ref(null)
  // 精确查找（GET /blacklist/lookup）：只保存最多一个命中用户，不保存候选列表
  const lookupResult = ref(null)
  const lookupLoading = ref(false)
  const lookupError = ref('')
  // 添加/解除在飞锁
  const submitting = ref(false)
  // 本会话内已知的已拉黑用户 id（fetchMine/add/remove 同步更新）。
  // 供订单「接单名单」和快捷拉黑弹窗即时标记「已拉黑」，不为此再拉全站用户列表。
  const knownBlockedIds = ref([])

  const knownBlockedSet = computed(
    () => new Set(knownBlockedIds.value.map((id) => Number(id))),
  )

  function isBlocked(userId) {
    if (userId == null) return false
    return knownBlockedSet.value.has(Number(userId))
  }

  function rememberBlockedIds(ids) {
    const next = new Set(knownBlockedIds.value.map((id) => Number(id)))
    for (const id of ids) {
      const numeric = Number(id)
      if (Number.isInteger(numeric) && numeric > 0) next.add(numeric)
    }
    knownBlockedIds.value = [...next]
  }

  function forgetBlockedId(userId) {
    const numeric = Number(userId)
    knownBlockedIds.value = knownBlockedIds.value.filter((id) => Number(id) !== numeric)
  }

  let mineRequestSeq = 0

  // GET /blacklist/mine?page=&page_size= -> {items, total, page, page_size, pages}
  async function fetchMine(page = 1, pageSize = 20) {
    const requestSeq = ++mineRequestSeq
    loading.value = true
    error.value = null
    try {
      const response = await api.get('/blacklist/mine', {
        params: { page, page_size: pageSize },
      })
      if (requestSeq !== mineRequestSeq) {
        return { success: true, stale: true }
      }
      const data = response.data || {}
      const nextItems = Array.isArray(data.items) ? data.items : []
      items.value = nextItems
      pagination.value = {
        page: Number(data.page) || page,
        pageSize: Number(data.page_size) || pageSize,
        total: Number(data.total) || nextItems.length,
        pages: Number(data.pages) || 0,
      }
      rememberBlockedIds(nextItems.map((item) => item.blocked_user_id))
      return { success: true, data: nextItems }
    } catch (err) {
      if (requestSeq !== mineRequestSeq) {
        return { success: false, stale: true, error: err.message }
      }
      error.value = err.message
      return { success: false, error: err.message, status: err.status }
    } finally {
      if (requestSeq === mineRequestSeq) {
        loading.value = false
      }
    }
  }

  // GET /blacklist/lookup?q= —— 只接受完全匹配的用户名或登录邮箱。
  // 404 映射为「未找到」固定文案；不返回任何候选列表。
  async function lookup(q) {
    const keyword = String(q || '').trim()
    lookupResult.value = null
    lookupError.value = ''
    if (keyword.length < 2) {
      lookupError.value = BLACKLIST_LOOKUP_TOO_SHORT
      return { success: false, invalid: true, error: lookupError.value }
    }
    lookupLoading.value = true
    try {
      const response = await api.get('/blacklist/lookup', { params: { q: keyword } })
      lookupResult.value = response.data || null
      return { success: true, data: lookupResult.value }
    } catch (err) {
      if (err?.status === 404) {
        lookupError.value = BLACKLIST_LOOKUP_NOT_FOUND
        return { success: false, notFound: true, error: lookupError.value }
      }
      lookupError.value = err?.message || '查找失败，请稍后重试'
      return { success: false, error: lookupError.value, status: err?.status }
    } finally {
      lookupLoading.value = false
    }
  }

  function clearLookup() {
    lookupResult.value = null
    lookupError.value = ''
  }

  // POST /blacklist { user_id, reason? } —— 重复拉黑后端幂等 200。
  // 成功即本地标记为已拉黑（取消标记只能靠 remove）。
  async function add(userId, reason) {
    const numericId = Number(userId)
    if (!Number.isInteger(numericId) || numericId <= 0) {
      return { success: false, error: '无效的用户' }
    }
    submitting.value = true
    error.value = null
    try {
      const payload = { user_id: numericId }
      const trimmedReason = String(reason || '').trim()
      if (trimmedReason) payload.reason = trimmedReason
      const response = await api.post('/blacklist', payload)
      rememberBlockedIds([numericId])
      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message, status: err.status }
    } finally {
      submitting.value = false
    }
  }

  // DELETE /blacklist/{blocked_user_id} —— 幂等 204；解除后立即恢复对方接单资格。
  async function remove(userId) {
    const numericId = Number(userId)
    if (!Number.isInteger(numericId) || numericId <= 0) {
      return { success: false, error: '无效的用户' }
    }
    submitting.value = true
    error.value = null
    try {
      await api.delete(`/blacklist/${numericId}`)
      forgetBlockedId(numericId)
      items.value = items.value.filter(
        (item) => Number(item.blocked_user_id) !== numericId,
      )
      pagination.value = {
        ...pagination.value,
        total: Math.max(0, Number(pagination.value.total || 0) - 1),
      }
      return { success: true }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message, status: err.status }
    } finally {
      submitting.value = false
    }
  }

  return {
    // State
    items,
    pagination,
    loading,
    error,
    lookupResult,
    lookupLoading,
    lookupError,
    submitting,
    knownBlockedIds,
    // Getters
    knownBlockedSet,
    // Actions
    isBlocked,
    fetchMine,
    lookup,
    clearLookup,
    add,
    remove,
  }
})
