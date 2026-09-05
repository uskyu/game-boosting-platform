// 来单 / 被接手声音提示：WebAudio Oscillator 短促 beep，无外部依赖，不加开关。
let audioContext = null
let newOrderAudio = null
let claimedAudio = null

function ensureContext() {
  try {
    if (audioContext) {
      return audioContext
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

function playStaticAudio(src, currentAudio) {
  try {
    const audio = currentAudio || new Audio(src)
    audio.currentTime = 0
    const result = audio.play()
    if (result && typeof result.catch === 'function') {
      result.catch(() => {})
    }
    return { audio, played: true }
  } catch {
    return { audio: currentAudio, played: false }
  }
}

function playNewOrderWebAudio() {
  beep({ frequency: 523, duration: 0.22, type: 'triangle' }).catch(() => {})
  beep({ frequency: 659, duration: 0.22, delay: 0.2, type: 'triangle' }).catch(() => {})
  beep({ frequency: 784, duration: 0.22, delay: 0.4, type: 'triangle' }).catch(() => {})
  beep({ frequency: 1047, duration: 0.4, delay: 0.6, type: 'triangle' }).catch(() => {})
}

function playClaimedWebAudio() {
  beep({ frequency: 880, duration: 0.2, type: 'triangle' }).catch(() => {})
  beep({ frequency: 660, duration: 0.45, delay: 0.22, type: 'triangle' }).catch(() => {})
}

// 来单：优先播放静态音频，失败时回退到 WebAudio 琶音
export function playNewOrder() {
  const result = playStaticAudio('/sounds/new-order.wav', newOrderAudio)
  newOrderAudio = result.audio
  if (!result.played) {
    playNewOrderWebAudio()
  }
}

// 被接手：优先播放静态音频，失败时回退到 WebAudio 下行双音
export function playClaimed() {
  const result = playStaticAudio('/sounds/order-claimed.wav', claimedAudio)
  claimedAudio = result.audio
  if (!result.played) {
    playClaimedWebAudio()
  }
}

// 真人聊天：短促双音，只提醒聊天消息，不与订单状态音混淆
export function playChatMessage() {
  beep({ frequency: 988, duration: 0.12, type: 'sine', gain: 0.12 }).catch(() => {})
  beep({ frequency: 1319, duration: 0.2, delay: 0.14, type: 'sine', gain: 0.12 }).catch(() => {})
}
