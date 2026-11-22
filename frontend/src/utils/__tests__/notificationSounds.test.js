/**
 * 通知提示音配置（sound.js）单元测试。
 *
 * 全部音频播放都 mock 掉（global.Audio + WebAudio 上下文），只验证配置合并、
 * 音量夹紧、静音短路、自定义 URL 选择与 getSoundConfig 的状态回显。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const played = []
let createdAudios = []

class FakeAudio {
  constructor() {
    this.src = ''
    this.preload = ''
    this._volume = 1
    this.currentTime = 0
    createdAudios.push(this)
  }

  get volume() {
    return this._volume
  }

  set volume(value) {
    this._volume = value
  }

  play() {
    played.push({ src: this.src, volume: this._volume })
    return Promise.resolve()
  }

  pause() {}
}

function fakeConfig() {
  vi.resetModules()
  played.length = 0
  createdAudios = []
  global.Audio = FakeAudio
  // 不提供 AudioContext：WebAudio 兜底路径自然变成无操作
  global.window = global.window || {}
  delete global.window.AudioContext
  delete global.window.webkitAudioContext
  return import('../sound')
}

function slotAudioCalls(src) {
  return played.filter((item) => item.src === src)
}

describe('notification sound configuration', () => {
  beforeEach(() => {
    played.length = 0
    createdAudios = []
  })

  it('merges a partial config over the built-in defaults', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({
      new_order: { url: '/uploads/sounds/new-order-abc12345.mp3', volume: 45 },
      chat_message: { enabled: false },
    })

    const config = sound.getSoundConfig()
    // 提交的槽位覆盖对应字段，未提交字段补默认值
    expect(config.new_order).toEqual({
      url: '/uploads/sounds/new-order-abc12345.mp3',
      volume: 45,
      enabled: true,
    })
    expect(config.chat_message).toEqual({ url: null, volume: 80, enabled: false })
    // 未提交的槽位保持内置默认
    expect(config.claimed).toEqual({ url: '/sounds/order-claimed.wav', volume: 80, enabled: true })
    expect(config.cancel_requested).toEqual({
      url: '/sounds/order-cancel-requested.wav',
      volume: 80,
      enabled: true,
    })
  })

  it('clamps volume into 0..100 and coerces non-numeric values', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({
      new_order: { volume: 180 },
      claimed: { volume: -20 },
      cancel_requested: { volume: '35abc' },
      chat_message: { volume: 100 },
    })

    const config = sound.getSoundConfig()
    expect(config.new_order.volume).toBe(100)
    expect(config.claimed.volume).toBe(0)
    // 非数字回落到默认 80
    expect(config.cancel_requested.volume).toBe(80)
    expect(config.chat_message.volume).toBe(100)
  })

  it('a disabled slot makes playNewOrder a no-op', async () => {
    const sound = await fakeConfig()
    // 模块导入时的默认预热已经建过 new-order 元素；清空记录后再看配置的影响
    played.length = 0
    createdAudios = []
    sound.configureSounds({ new_order: { enabled: false } })

    sound.playNewOrder()

    expect(played).toEqual([])
    // 静音槽位不再预热（即便此前缓存里已有元素，也不会重新创建/播放）
    expect(createdAudios.some((audio) => audio.src.includes('new-order'))).toBe(false)
  })

  it('uses the custom url when one is configured', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({ claimed: { url: '/uploads/sounds/claimed-zz99.mp3', volume: 65 } })

    sound.playClaimed()

    const calls = slotAudioCalls('/uploads/sounds/claimed-zz99.mp3')
    expect(calls.length).toBeGreaterThan(0)
    expect(calls[0].volume).toBeCloseTo(0.65, 5)
    // 内置音频不应被使用
    expect(slotAudioCalls('/sounds/order-claimed.wav')).toEqual([])
  })

  it('falls back to the built-in file and applies the configured volume', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({ cancel_requested: { volume: 40 } })

    sound.playCancelRequested()

    const calls = slotAudioCalls('/sounds/order-cancel-requested.wav')
    expect(calls.length).toBeGreaterThan(0)
    expect(calls[0].volume).toBeCloseTo(0.4, 5)
  })

  it('plays the custom chat url and silences synthesized beeps when muted', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({ chat_message: { url: '/uploads/sounds/chat-beep.wav', volume: 50 } })

    sound.playChatMessage()
    expect(slotAudioCalls('/uploads/sounds/chat-beep.wav')[0].volume).toBeCloseTo(0.5, 5)

    sound.configureSounds({ chat_message: { url: '/uploads/sounds/chat-beep.wav', enabled: false } })
    played.length = 0
    sound.playChatMessage()
    expect(played).toEqual([])
  })

  it('playChatMessage without a custom url uses the synthesized default (no static file)', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({})

    sound.playChatMessage()

    // 默认聊天音是合成音：不会去播任何静态文件
    expect(played).toEqual([])
  })

  it('prewarms custom urls but skips muted slots', async () => {
    const sound = await fakeConfig()
    createdAudios = []
    sound.configureSounds({
      new_order: { url: '/uploads/sounds/new-order-q1.mp3' },
      claimed: { url: '/uploads/sounds/claimed-muted.mp3', enabled: false },
    })

    const sources = createdAudios.map((audio) => audio.src)
    expect(sources).toContain('/uploads/sounds/new-order-q1.mp3')
    expect(sources).not.toContain('/uploads/sounds/claimed-muted.mp3')
    // 内置默认音频在模块导入时已预热，configureSounds 不会重复创建元素
    expect(sources).not.toContain('/sounds/order-cancel-requested.wav')
    expect(sources.length).toBe(1)
  })

  it('getSoundConfig reflects merged state and is a copy', async () => {
    const sound = await fakeConfig()
    sound.configureSounds({ claimed: { volume: 20 } })

    const first = sound.getSoundConfig()
    expect(first.claimed.volume).toBe(20)

    // 修改返回对象不影响模块内部状态
    first.claimed.volume = 99
    expect(sound.getSoundConfig().claimed.volume).toBe(20)

    // 重新配置会完整覆盖上一轮值
    sound.configureSounds({ new_order: { volume: 10 } })
    expect(sound.getSoundConfig().new_order.volume).toBe(10)
    expect(sound.getSoundConfig().claimed.volume).toBe(80)
  })

  it('ignores malformed config input', async () => {
    const sound = await fakeConfig()
    sound.configureSounds(null)
    expect(sound.getSoundConfig().new_order.url).toBe('/sounds/new-order.wav')

    sound.configureSounds({ new_order: 'not-an-object', claimed: { url: '' } })
    expect(sound.getSoundConfig().new_order.volume).toBe(80)
    // 空串 URL 视为「用内置默认」
    expect(sound.getSoundConfig().claimed.url).toBe('/sounds/order-claimed.wav')
  })
})
