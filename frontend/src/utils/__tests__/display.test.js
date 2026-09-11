import { afterEach, describe, expect, it } from 'vitest'

import {
  formatDueCountdown,
  getAcceptWaitMeta,
  getDueCountdownParts,
  resetServerClock,
  serverNow,
  syncServerTime,
} from '../display'

const SERVER_MS = new Date('2026-09-11T12:00:00Z').getTime()

afterEach(() => {
  resetServerClock()
})

describe('serverNow / syncServerTime', () => {
  it('设备本地时钟快 5 秒时，校准后 serverNow 回到服务器时间（±0.5s 取中）', () => {
    const originalNow = Date.now
    // 本机 now = 真实服务器时间 + 500 + 5000ms（快 5 秒；Date 头只归整到秒）
    Date.now = () => SERVER_MS + 500 + 5000
    try {
      syncServerTime(new Date(SERVER_MS).toUTCString())
      expect(serverNow()).toBeGreaterThanOrEqual(SERVER_MS)
      expect(serverNow()).toBeLessThanOrEqual(SERVER_MS + 1000)
    } finally {
      Date.now = originalNow
    }
  })

  it('非法 Date 头不改变偏移', () => {
    expect(syncServerTime('not a date')).toBe(false)
    // 两次取时允许毫秒级差值
    expect(Math.abs(serverNow() - Date.now())).toBeLessThanOrEqual(2)
  })
})

describe('getAcceptWaitMeta', () => {
  it('设备时钟快 5 秒时，剩余等待秒数仍按服务器时间计算（配置 25 秒显示 25 秒）', () => {
    const originalNow = Date.now
    Date.now = () => SERVER_MS + 500 + 5000 // 本地时钟快 5 秒
    try {
      syncServerTime(new Date(SERVER_MS).toUTCString())
      const order = {
        accept_wait_seconds: 25,
        accept_available_at: new Date(SERVER_MS + 25_000).toISOString(),
      }
      const meta = getAcceptWaitMeta(order)
      expect(meta.total).toBe(25)
      expect(meta.remaining).toBeGreaterThanOrEqual(24) // ≈25（±0.5s 取中）
      expect(meta.remaining).toBeLessThanOrEqual(25)
      expect(meta.state).toBe('waiting')
    } finally {
      Date.now = originalNow
    }
  })
})

describe('formatDueCountdown / getDueCountdownParts', () => {
  it('分解时分秒', () => {
    expect(getDueCountdownParts(new Date(SERVER_MS + 3661_000).toISOString(), SERVER_MS)).toEqual({
      hours: 1,
      minutes: 1,
      seconds: 1,
    })
  })

  it('到点或无效时间返回 null', () => {
    expect(getDueCountdownParts(new Date(SERVER_MS - 1000).toISOString(), SERVER_MS)).toBeNull()
    expect(getDueCountdownParts(null, SERVER_MS)).toBeNull()
  })

  it('格式化：小时/分秒/纯秒', () => {
    expect(formatDueCountdown(new Date(SERVER_MS + 3661_000).toISOString(), SERVER_MS)).toBe('1小时01分')
    expect(formatDueCountdown(new Date(SERVER_MS + 125_000).toISOString(), SERVER_MS)).toBe('2分05秒')
    expect(formatDueCountdown(new Date(SERVER_MS + 45_000).toISOString(), SERVER_MS)).toBe('45秒')
  })
})
