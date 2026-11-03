/**
 * 排行榜悬浮球纵向位置计算。
 *
 * 悬浮球只能在「顶部导航栏」与「底部 Tab 栏」之间的可见区域上下移动：
 * 顶部留出 HEADER_GAP，底部留出 TABBAR_GAP，避免被导航栏遮住后无法点击。
 */

export const LEADERBOARD_FLOAT_HEIGHT = 78

const EDGE_GAP = 8
const HEADER_GAP = 10
const TABBAR_GAP = 12

export function resolveLeaderboardFloatBounds({ viewportHeight, headerBottom = null, tabbarTop = null }) {
  const height = Number(viewportHeight) || 0
  let minY = EDGE_GAP
  let maxY = height - LEADERBOARD_FLOAT_HEIGHT - EDGE_GAP

  if (Number.isFinite(headerBottom) && headerBottom > 0) {
    minY = Math.max(minY, headerBottom + HEADER_GAP)
  }

  // tabbarTop 为空或 0 表示当前页面没有固定底部导航栏。
  if (Number.isFinite(tabbarTop) && tabbarTop > 0) {
    maxY = Math.min(maxY, tabbarTop - LEADERBOARD_FLOAT_HEIGHT - TABBAR_GAP)
  }

  // 极端矮屏放不下时退回基础可视范围，至少保证悬浮球完整可点。
  if (maxY < minY) {
    minY = EDGE_GAP
    maxY = Math.max(minY, height - LEADERBOARD_FLOAT_HEIGHT - EDGE_GAP)
  }

  return { minY, maxY }
}

export function clampLeaderboardFloatY(y, bounds) {
  const numeric = Number(y)
  if (!Number.isFinite(numeric)) {
    return bounds.minY
  }
  return Math.min(Math.max(numeric, bounds.minY), bounds.maxY)
}
