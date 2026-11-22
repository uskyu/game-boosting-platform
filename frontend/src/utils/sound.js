// 通知提示音（来单 / 被接手 / 申请取消 / 聊天消息）：支持后台按槽位自定义音频
// （MP3/WAV）、音量与静音。自定义 URL 为空时沿用内置默认：三声订单语音播放
// public/sounds/*.wav，聊天消息用 WebAudio 合成提示音。静态音频启动即预热
// （preload=auto），播放直接用已缓冲的 <audio> 元素——不再每播一次拉一遍文件。

// 内置默认：url=null 表示「使用内置音频」（chat_message 为合成音）
const DEFAULTS = {
  new_order: { url: '/sounds/new-order.wav', volume: 80, enabled: true },
  claimed: { url: '/sounds/order-claimed.wav', volume: 80, enabled: true },
  cancel_requested: { url: '/sounds/order-cancel-requested.wav', volume: 80, enabled: true },
  chat_message: { url: null, volume: 80, enabled: true }, // null -> synthesized beep
}

const SLOT_KEYS = Object.keys(DEFAULTS)

function clampVolume(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return DEFAULTS.new_order.volume
  return Math.min(100, Math.max(0, Math.round(number)))
}

// 槽位对象逐层克隆：对外返回的配置可以被调用方随意修改，不会污染模块状态
function cloneConfig(config) {
  const copy = {}
  SLOT_KEYS.forEach((slotKey) => {
    copy[slotKey] = { ...config[slotKey] }
  })
  return copy
}

// 当前生效配置（克隆自 DEFAULTS，避免调用方改到内置默认）
let soundConfig = cloneConfig(DEFAULTS)

// 静态语音缓存：src -> <audio>（整个模块生命周期复用同一个元素）
const staticAudio = {}

function getStaticAudio(src) {
  if (staticAudio[src]) {
    return staticAudio[src]
  }
  const audio = new Audio()
  audio.preload = 'auto'
  audio.src = src
  staticAudio[src] = audio
  return audio
}

// 音量按百分比传（0-100），<audio> 需要 0-1
function applyVolume(audio, volume) {
  try {
    audio.volume = Math.min(1, Math.max(0, Number(volume) / 100 || 0))
  } catch {
    // 个别浏览器/测试桩不接受 volume 赋值，忽略即可
  }
  return audio
}

function playStaticAudio(src, volume = DEFAULTS.new_order.volume) {
  try {
    const audio = getStaticAudio(src)
    applyVolume(audio, volume)
    audio.currentTime = 0
    const result = audio.play()
    if (result && typeof result.catch === 'function') {
      result.catch(() => {})
    }
    return true
  } catch {
    return false
  }
}

// 预热播报语音：preload 不需要用户手势，页面加载即开始下载，
// 等第一条订单到来时语音早已缓冲完毕（nginx 对 /sounds/ 另有 1 天强缓存）。
// 只预热「启用且不是内置默认」的槽位之外的默认音频：静音槽位不预热，
// 自定义 URL 的槽位按下发配置预热（见 configureSounds）。
function prewarmDefaults() {
  if (typeof Audio === 'undefined') return
  SLOT_KEYS.forEach((slotKey) => {
    const slot = soundConfig[slotKey]
    if (!slot || !slot.enabled) return
    if (slot.url) {
      getStaticAudio(slot.url)
      return
    }
    const fallback = DEFAULTS[slotKey].url
    // chat_message 的默认是合成音，没有静态文件可预热
    if (fallback) getStaticAudio(fallback)
  })
}

// 后台配置合并：缺失槽位/字段用默认值补齐，音量夹到 0-100，
// 然后重建音频缓存（换过文件的自定义 URL 也要预热，静音槽位跳过）。
export function configureSounds(soundsFromSettings) {
  const source = soundsFromSettings && typeof soundsFromSettings === 'object'
    ? soundsFromSettings
    : {}
  const next = {}
  SLOT_KEYS.forEach((slotKey) => {
    const fallback = DEFAULTS[slotKey]
    const incoming = source[slotKey]
    const slot = incoming && typeof incoming === 'object' ? incoming : {}
    const url = typeof slot.url === 'string' && slot.url.trim() ? slot.url.trim() : fallback.url
    next[slotKey] = {
      url,
      volume: slot.volume === undefined ? fallback.volume : clampVolume(slot.volume),
      enabled: slot.enabled === undefined ? fallback.enabled : slot.enabled !== false,
    }
  })
  soundConfig = next
  prewarmDefaults()
  return getSoundConfig()
}

export function getSoundConfig() {
  return cloneConfig(soundConfig)
}

// WebAudio 合成音（静态语音播放失败时的兜底，也是聊天消息的内置音频）
let audioContext = null

function ensureContext() {
  try {
    if (audioContext) {
      return audioContext
    }
    if (typeof window === 'undefined') {
      return null
    }
    const Ctor = window.AudioContext || window.webkitAudioContext
    if (!Ctor) {
      return null
    }
    audioContext = new Ctor()
    return audioContext
  } catch {
    return null
  }
}

// 首次用户手势后解锁自动播放限制：常驻监听，每次都尝试 resume
function tryUnlockAudio() {
  try {
    const ctx = ensureContext()
    if (ctx && ctx.state === 'suspended') {
      const ret = ctx.resume()
      if (ret && typeof ret.catch === 'function') {
        ret.catch(() => {})
      }
    }
  } catch {
    // 解锁失败忽略，等下次播放时再试
  }
}

if (typeof document !== 'undefined') {
  ;['click', 'touchstart', 'keydown'].forEach((evt) => {
    document.addEventListener(evt, tryUnlockAudio, { capture: true })
  })
}

async function beep({ frequency = 880, duration = 0.15, delay = 0, type = 'sine', gain = 0.15 }) {
  try {
    const ctx = ensureContext()
    if (!ctx) {
      return
    }
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume()
      } catch {
        // resume 失败忽略，下面再判断 state
      }
      if (ctx.state === 'suspended') {
        return
      }
    }
    const startAt = ctx.currentTime + delay
    const oscillator = ctx.createOscillator()
    const amplifier = ctx.createGain()
    oscillator.type = type
    oscillator.frequency.setValueAtTime(frequency, startAt)
    amplifier.gain.setValueAtTime(0.0001, startAt)
    amplifier.gain.exponentialRampToValueAtTime(gain, startAt + 0.01)
    amplifier.gain.exponentialRampToValueAtTime(0.0001, startAt + duration)
    oscillator.connect(amplifier)
    amplifier.connect(ctx.destination)
    oscillator.start(startAt)
    oscillator.stop(startAt + duration + 0.05)
  } catch {
    // 出声失败静默忽略，不影响业务
  }
}

// 合成音的音量：默认 beep gain 0.15 对应 80% 左右，按百分比等比缩放
function beepGain(volume, baseGain) {
  return baseGain * Math.min(1, Math.max(0, Number(volume) / 100 || 0))
}

function playNewOrderWebAudio(volume = DEFAULTS.new_order.volume) {
  const gain = beepGain(volume, 0.15)
  beep({ frequency: 523, duration: 0.22, type: 'triangle', gain }).catch(() => {})
  beep({ frequency: 659, duration: 0.22, delay: 0.2, type: 'triangle', gain }).catch(() => {})
  beep({ frequency: 784, duration: 0.22, delay: 0.4, type: 'triangle', gain }).catch(() => {})
  beep({ frequency: 1047, duration: 0.4, delay: 0.6, type: 'triangle', gain }).catch(() => {})
}

function playClaimedWebAudio(volume = DEFAULTS.claimed.volume) {
  const gain = beepGain(volume, 0.15)
  beep({ frequency: 880, duration: 0.2, type: 'triangle', gain }).catch(() => {})
  beep({ frequency: 660, duration: 0.45, delay: 0.22, type: 'triangle', gain }).catch(() => {})
}

// 取消申请兜底音：与「被接手」区分开的下行双音，避免两种提示听起来一样
function playCancelRequestedWebAudio(volume = DEFAULTS.cancel_requested.volume) {
  const gain = beepGain(volume, 0.15)
  beep({ frequency: 740, duration: 0.2, type: 'triangle', gain }).catch(() => {})
  beep({ frequency: 494, duration: 0.45, delay: 0.22, type: 'triangle', gain }).catch(() => {})
}

// 统一播放：静音直接返回；自定义 URL 优先；静态音频不可用时回退合成音
function playSlot(slotKey, webAudioFallback) {
  const slot = soundConfig[slotKey] || DEFAULTS[slotKey]
  if (!slot.enabled) return
  const src = slot.url || DEFAULTS[slotKey].url
  if (src) {
    if (playStaticAudio(src, slot.volume)) return
  }
  webAudioFallback(slot.volume)
}

// 来单：优先播放静态音频，失败时回退到 WebAudio 琶音
export function playNewOrder() {
  playSlot('new_order', playNewOrderWebAudio)
}

// 被接手：优先播放静态音频，失败时回退到 WebAudio 下行双音
export function playClaimed() {
  playSlot('claimed', playClaimedWebAudio)
}

// 取消申请：优先播放静态音频（老板提供的「有订单申请取消了请及时查看」），
// 失败时回退到 WebAudio 下行双音
export function playCancelRequested() {
  playSlot('cancel_requested', playCancelRequestedWebAudio)
}

// 真人聊天：默认是短促双音（不与订单状态音混淆）；自定义了音频就播音频
export function playChatMessage() {
  const slot = soundConfig.chat_message || DEFAULTS.chat_message
  if (!slot.enabled) return
  if (slot.url) {
    if (playStaticAudio(slot.url, slot.volume)) return
  }
  const gain = beepGain(slot.volume, 0.12)
  beep({ frequency: 988, duration: 0.12, type: 'sine', gain }).catch(() => {})
  beep({ frequency: 1319, duration: 0.2, delay: 0.14, type: 'sine', gain }).catch(() => {})
}

// 启动预热（与旧行为一致：页面加载就把三个内置订单语音下好）
prewarmDefaults()
