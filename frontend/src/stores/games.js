import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import api from '@/utils/api'
import { GAME_CATEGORY_META } from '@/utils/gameCatalog'

function buildEmptyPagination() {
  return {
    page: 1,
    pageSize: 100,
    total: 0,
    pages: 0,
  }
}

export const useGamesStore = defineStore('games', () => {
  const games = ref([])
  const catalogGames = ref([])
  const currentGame = ref(null)
  const loading = ref(false)
  const error = ref(null)
  const pagination = ref(buildEmptyPagination())

  const gameMap = computed(() => {
    return catalogGames.value.reduce((result, game) => {
      result[game.id] = game
      return result
    }, {})
  })

  const gamesByCategory = computed(() => {
    return catalogGames.value.reduce((result, game) => {
      if (!result[game.category]) {
        result[game.category] = []
      }
      result[game.category].push(game)
      return result
    }, {})
  })

  const categories = computed(() => {
    return GAME_CATEGORY_META.map((meta) => {
      const items = gamesByCategory.value[meta.value] || []
      return {
        ...meta,
        count: items.length,
        games: items,
      }
    })
  })

  const hasGames = computed(() => catalogGames.value.length > 0)
  const randomGame = computed(() => {
    if (!catalogGames.value.length) {
      return null
    }

    const index = Math.floor(Math.random() * catalogGames.value.length)
    return catalogGames.value[index]
  })

  async function fetchGames(category = '', platform = '', options = {}) {
    loading.value = true
    error.value = null

    const params = {
      page: 1,
      page_size: options.pageSize || 100,
    }

    if (category) {
      params.category = category
    }

    if (platform) {
      params.platform = platform
    }

    try {
      const response = await api.get('/games/', { params })
      games.value = response.data.items || []
      pagination.value = {
        page: response.data.page ?? 1,
        pageSize: response.data.page_size ?? params.page_size,
        total: response.data.total ?? games.value.length,
        pages: response.data.pages ?? 1,
      }

      if (!category && !platform) {
        catalogGames.value = response.data.items || []
      } else if (!catalogGames.value.length) {
        catalogGames.value = response.data.items || []
      }

      return { success: true, data: games.value }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function ensureCatalog(options = {}) {
    if (catalogGames.value.length && !options.force) {
      return { success: true, data: catalogGames.value }
    }

    return fetchGames('', '', options)
  }

  async function fetchGame(id) {
    loading.value = true
    error.value = null

    try {
      const response = await api.get(`/games/${id}`)
      currentGame.value = response.data

      const existingIndex = catalogGames.value.findIndex((game) => game.id === response.data.id)
      if (existingIndex === -1) {
        catalogGames.value.push(response.data)
      } else {
        catalogGames.value.splice(existingIndex, 1, response.data)
      }

      return { success: true, data: response.data }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  async function updateGame(id, payload) {
    loading.value = true
    error.value = null

    try {
      const response = await api.put(`/games/${id}`, payload)
      const nextValue = response.data
      const catalogIndex = catalogGames.value.findIndex((game) => game.id === id)
      if (catalogIndex === -1) {
        catalogGames.value.push(nextValue)
      } else {
        catalogGames.value.splice(catalogIndex, 1, nextValue)
      }

      const gamesIndex = games.value.findIndex((game) => game.id === id)
      if (gamesIndex !== -1) {
        games.value.splice(gamesIndex, 1, nextValue)
      }

      if (currentGame.value?.id === id) {
        currentGame.value = nextValue
      }

      return { success: true, data: nextValue }
    } catch (err) {
      error.value = err.message
      return { success: false, error: err.message }
    } finally {
      loading.value = false
    }
  }

  function getGameById(id) {
    return gameMap.value[id] || null
  }

  function clearCurrentGame() {
    currentGame.value = null
  }

  return {
    games,
    catalogGames,
    currentGame,
    loading,
    error,
    pagination,
    categories,
    gameMap,
    gamesByCategory,
    hasGames,
    randomGame,
    fetchGames,
    ensureCatalog,
    fetchGame,
    updateGame,
    getGameById,
    clearCurrentGame,
  }
})
