// 来单 / 被接手声音提示：WebAudio Oscillator 短促 beep，无外部依赖，不加开关。
let audioContext = null

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

// 来单：上行琶音四连音（特色长音，一听就是来单了）
export function playNewOrder() {
  beep({ frequency: 523, duration: 0.22, type: 'triangle' }).catch(() => {})
  beep({ frequency: 659, duration: 0.22, delay: 0.2, type: 'triangle' }).catch(() => {})
  beep({ frequency: 784, duration: 0.22, delay: 0.4, type: 'triangle' }).catch(() => {})
  beep({ frequency: 1047, duration: 0.4, delay: 0.6, type: 'triangle' }).catch(() => {})
}

// 被接手：下行双音收尾（和来单区分）
export function playClaimed() {
  beep({ frequency: 880, duration: 0.2, type: 'triangle' }).catch(() => {})
  beep({ frequency: 660, duration: 0.45, delay: 0.22, type: 'triangle' }).catch(() => {})
}

// 真人聊天：短促双音，只提醒聊天消息，不与订单状态音混淆
export function playChatMessage() {
  beep({ frequency: 988, duration: 0.12, type: 'sine', gain: 0.12 }).catch(() => {})
  beep({ frequency: 1319, duration: 0.2, delay: 0.14, type: 'sine', gain: 0.12 }).catch(() => {})
}
