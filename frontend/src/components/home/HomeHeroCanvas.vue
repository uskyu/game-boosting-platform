<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  accent: {
    type: String,
    default: '#ff4655',
  },
})

const canvasRef = ref(null)

let animationFrame = 0
let resizeHandler = null
let pointerHandler = null
let leaveHandler = null
let reduceMotionQuery = null
let width = 0
let height = 0
let dpr = 1
let particles = []
const pointer = { x: 0.5, y: 0.42 }

function hexToRgb(hex) {
  const safe = String(hex || '').replace('#', '').trim()
  if (safe.length !== 6) {
    return { r: 255, g: 70, b: 85 }
  }

  return {
    r: Number.parseInt(safe.slice(0, 2), 16),
    g: Number.parseInt(safe.slice(2, 4), 16),
    b: Number.parseInt(safe.slice(4, 6), 16),
  }
}

function buildParticles() {
  const count = Math.max(22, Math.min(48, Math.floor(width / 42)))
  particles = Array.from({ length: count }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    radius: Math.random() * 1.8 + 0.6,
    speed: Math.random() * 0.28 + 0.1,
    drift: (Math.random() - 0.5) * 0.18,
    alpha: Math.random() * 0.45 + 0.15,
  }))
}

function resize() {
  const canvas = canvasRef.value
  if (!canvas) {
    return
  }

  const rect = canvas.getBoundingClientRect()
  width = rect.width
  height = rect.height
  dpr = Math.min(window.devicePixelRatio || 1, 2)

  canvas.width = Math.floor(width * dpr)
  canvas.height = Math.floor(height * dpr)

  const context = canvas.getContext('2d')
  context.setTransform(dpr, 0, 0, dpr, 0, 0)
  buildParticles()
}

function drawGrid(context, rgb, time) {
  const horizon = height * (0.62 + (pointer.y - 0.5) * 0.04)
  const lift = (pointer.x - 0.5) * 80

  context.save()
  context.globalAlpha = 0.5
  context.strokeStyle = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.18)`
  context.lineWidth = 1

  for (let i = -7; i <= 7; i += 1) {
    context.beginPath()
    context.moveTo(width * 0.5 + lift, horizon)
    context.lineTo(width * 0.5 + lift + i * 150, height)
    context.stroke()
  }

  for (let i = 0; i < 7; i += 1) {
    const progress = i / 6
    const eased = progress ** 1.6
    const y = horizon + eased * (height - horizon)
    context.beginPath()
    context.moveTo(0, y + Math.sin(time * 0.0004 + i * 0.35) * 2)
    context.lineTo(width, y)
    context.stroke()
  }

  context.restore()
}

function drawParticles(context, rgb, time) {
  particles.forEach((particle, index) => {
    particle.y -= particle.speed
    particle.x += particle.drift + Math.sin(time * 0.0004 + index) * 0.04

    if (particle.y < -20) {
      particle.y = height + 20
      particle.x = Math.random() * width
    }

    if (particle.x < -20) {
      particle.x = width + 20
    }

    if (particle.x > width + 20) {
      particle.x = -20
    }

    context.beginPath()
    context.fillStyle = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${particle.alpha})`
    context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2)
    context.fill()
  })
}

function render(time = 0) {
  const canvas = canvasRef.value
  if (!canvas) {
    return
  }

  const context = canvas.getContext('2d')
  if (!context) {
    return
  }

  const rgb = hexToRgb(props.accent)
  context.clearRect(0, 0, width, height)

  const glow = context.createRadialGradient(
    width * (0.7 + (pointer.x - 0.5) * 0.08),
    height * (0.3 + (pointer.y - 0.5) * 0.05),
    0,
    width * 0.7,
    height * 0.36,
    width * 0.42
  )

  glow.addColorStop(0, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.22)`)
  glow.addColorStop(0.5, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`)
  glow.addColorStop(1, 'rgba(0, 0, 0, 0)')

  context.fillStyle = glow
  context.fillRect(0, 0, width, height)

  drawGrid(context, rgb, time)
  drawParticles(context, rgb, time)

  context.save()
  context.globalCompositeOperation = 'screen'
  context.strokeStyle = `rgba(255, 255, 255, 0.08)`
  context.lineWidth = 1
  context.beginPath()
  context.moveTo(0, height * 0.76 + Math.sin(time * 0.0008) * 4)
  context.bezierCurveTo(
    width * 0.28,
    height * 0.69,
    width * 0.56,
    height * 0.86,
    width,
    height * 0.73
  )
  context.stroke()
  context.restore()

  if (!reduceMotionQuery?.matches) {
    animationFrame = window.requestAnimationFrame(render)
  }
}

onMounted(() => {
  const canvas = canvasRef.value
  if (!canvas) {
    return
  }

  const hero = canvas.parentElement
  reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')

  resizeHandler = () => {
    resize()
    if (reduceMotionQuery.matches) {
      render(performance.now())
    }
  }

  pointerHandler = (event) => {
    const rect = hero?.getBoundingClientRect()
    if (!rect) {
      return
    }

    pointer.x = (event.clientX - rect.left) / rect.width
    pointer.y = (event.clientY - rect.top) / rect.height
  }

  leaveHandler = () => {
    pointer.x = 0.5
    pointer.y = 0.42
  }

  resize()
  hero?.addEventListener('pointermove', pointerHandler)
  hero?.addEventListener('pointerleave', leaveHandler)
  window.addEventListener('resize', resizeHandler)

  if (reduceMotionQuery.matches) {
    render(performance.now())
    return
  }

  animationFrame = window.requestAnimationFrame(render)
})

watch(
  () => props.accent,
  () => {
    if (reduceMotionQuery?.matches) {
      render(performance.now())
    }
  }
)

onBeforeUnmount(() => {
  const hero = canvasRef.value?.parentElement

  if (animationFrame) {
    window.cancelAnimationFrame(animationFrame)
  }

  if (resizeHandler) {
    window.removeEventListener('resize', resizeHandler)
  }

  if (pointerHandler) {
    hero?.removeEventListener('pointermove', pointerHandler)
  }

  if (leaveHandler) {
    hero?.removeEventListener('pointerleave', leaveHandler)
  }
})
</script>

<template>
  <canvas ref="canvasRef" class="pointer-events-none absolute inset-0 h-full w-full opacity-80 mix-blend-screen" />
</template>
