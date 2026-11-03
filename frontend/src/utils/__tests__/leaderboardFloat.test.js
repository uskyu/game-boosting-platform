import { describe, expect, it } from 'vitest'

import {
  clampLeaderboardFloatY,
  LEADERBOARD_FLOAT_HEIGHT,
  resolveLeaderboardFloatBounds,
} from '../leaderboardFloat'

describe('leaderboard floating entry bounds', () => {
  it('desktop keeps the ball below the sticky header and above the window edge', () => {
    const bounds = resolveLeaderboardFloatBounds({
      viewportHeight: 720,
      headerBottom: 89,
    })

    expect(bounds).toEqual({ minY: 99, maxY: 720 - LEADERBOARD_FLOAT_HEIGHT - 8 })
  })

  it('mobile reserves space for the fixed bottom tab bar', () => {
    const bounds = resolveLeaderboardFloatBounds({
      viewportHeight: 844,
      headerBottom: 61,
      tabbarTop: 795,
    })

    expect(bounds.minY).toBe(71)
    expect(bounds.maxY).toBe(705)
    expect(bounds.maxY + LEADERBOARD_FLOAT_HEIGHT).toBeLessThan(795)
  })

  it('clamps saved positions that would hide under either navigation bar', () => {
    const bounds = resolveLeaderboardFloatBounds({
      viewportHeight: 844,
      headerBottom: 61,
      tabbarTop: 795,
    })

    expect(clampLeaderboardFloatY(0, bounds)).toBe(71)
    expect(clampLeaderboardFloatY(5000, bounds)).toBe(705)
    expect(clampLeaderboardFloatY('bad', bounds)).toBe(71)
  })

  it('falls back to the visible viewport when the navigation bars leave no room', () => {
    const bounds = resolveLeaderboardFloatBounds({
      viewportHeight: 100,
      headerBottom: 80,
      tabbarTop: 40,
    })

    expect(bounds.minY).toBe(8)
    expect(bounds.maxY).toBe(14)
  })
})
