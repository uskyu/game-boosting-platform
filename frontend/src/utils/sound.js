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

// 来单：上行琶音四连音（特色长音，一听就是来单了）
export function playNewOrder() {
  beep({ frequency: 523, duration: 0.22, type: 'triangle' })
  beep({ frequency: 659, duration: 0.22, delay: 0.2, type: 'triangle' })
  beep({ frequency: 784, duration: 0.22, delay: 0.4, type: 'triangle' })
  beep({ frequency: 1047, duration: 0.4, delay: 0.6, type: 'triangle' })
}

// 被接手：下行双音收尾（和来单区分）
export function playClaimed() {
  beep({ frequency: 880, duration: 0.2, type: 'triangle' })
  beep({ frequency: 660, duration: 0.45, delay: 0.22, type: 'triangle' })
}
