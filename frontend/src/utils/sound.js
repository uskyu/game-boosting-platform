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

// 首次用户手势后解锁自动播放限制
if (typeof document !== 'undefined') {
  document.addEventListener('click', () => {
    try {
      const ctx = ensureContext()
      if (ctx && ctx.state === 'suspended') {
        ctx.resume()
      }
    } catch {
      // 解锁失败忽略，等下次播放时再试
    }
  }, { once: true })
}

function beep({ frequency = 880, duration = 0.15, delay = 0, type = 'sine', gain = 0.15 }) {
  try {
    const ctx = ensureContext()
    if (!ctx) {
      return
    }
    if (ctx.state === 'suspended') {
      ctx.resume()
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

// 来单：一声短促高音
export function playNewOrder() {
  beep({ frequency: 880, duration: 0.18 })
}

// 被接手：两声区分（低→高）
export function playClaimed() {
  beep({ frequency: 660, duration: 0.12 })
  beep({ frequency: 990, duration: 0.16, delay: 0.16 })
}
