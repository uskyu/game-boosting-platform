<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  isTyping: { type: Boolean, default: false },
  showPassword: { type: Boolean, default: false },
  passwordLength: { type: Number, default: 0 },
})

/* ── theme colors（语义变量，随主题切换） ── */
const C = {
  primary: 'var(--primary)',
  neutral: 'var(--text-3)',
  warm: 'var(--warning)',
  info: 'var(--info)',
  pupil: 'var(--text-1)',
  sclera: 'var(--surface)',
}

/* ── mouse tracking (single listener, RAF-throttled) ── */
const mx = ref(0)
const my = ref(0)
let pendingX = 0
let pendingY = 0
let rafId = null

function onMouse(e) {
  pendingX = e.clientX
  pendingY = e.clientY
  if (!rafId) {
    rafId = requestAnimationFrame(() => {
      mx.value = pendingX
      my.value = pendingY
      rafId = null
    })
  }
}

/* ── character body refs ── */
const pinkEl = ref(null)
const neutralEl = ref(null)
const warmEl = ref(null)
const infoEl = ref(null)

/* ── blink state ── */
const pinkBlink = ref(false)
const darkBlink = ref(false)

/* ── interaction state ── */
const lookingAtEach = ref(false)
const pinkPeeking = ref(false)

/* ── derived state ── */
const hiding = computed(() => props.passwordLength > 0 && !props.showPassword)
const showing = computed(() => props.passwordLength > 0 && props.showPassword)

/* ── position math ── */
function pos(el) {
  if (!el) return { fx: 0, fy: 0, skew: 0 }
  const r = el.getBoundingClientRect()
  const dx = mx.value - (r.left + r.width / 2)
  const dy = my.value - (r.top + r.height / 3)
  return {
    fx: Math.max(-15, Math.min(15, dx / 20)),
    fy: Math.max(-10, Math.min(10, dy / 30)),
    skew: Math.max(-6, Math.min(6, -dx / 120)),
  }
}

function pupilOff(el, max = 5) {
  if (!el) return { x: 0, y: 0 }
  const r = el.getBoundingClientRect()
  const dx = mx.value - (r.left + r.width / 2)
  const dy = my.value - (r.top + r.height / 3)
  const d = Math.min(Math.sqrt(dx * dx + dy * dy), max)
  const a = Math.atan2(dy, dx)
  return { x: Math.cos(a) * d, y: Math.sin(a) * d }
}

/* ── computed positions per character ── */
const pp = computed(() => pos(pinkEl.value))
const dp = computed(() => pos(neutralEl.value))
const gp = computed(() => pos(warmEl.value))
const cp = computed(() => pos(infoEl.value))

const pPupil = computed(() => pupilOff(pinkEl.value, 5))
const dPupil = computed(() => pupilOff(neutralEl.value, 4))
const gPupil = computed(() => pupilOff(warmEl.value, 5))
const cPupil = computed(() => pupilOff(infoEl.value, 5))

/* ── forced look directions ── */
const pinkForce = computed(() => {
  if (showing.value) return pinkPeeking.value ? { x: 4, y: 5 } : { x: -4, y: -4 }
  if (lookingAtEach.value) return { x: 3, y: 4 }
  return null
})
const darkForce = computed(() => {
  if (showing.value) return { x: -4, y: -4 }
  if (lookingAtEach.value) return { x: 0, y: -4 }
  return null
})
const goldForce = computed(() => (showing.value ? { x: -5, y: -4 } : null))
const cyanForce = computed(() => (showing.value ? { x: -5, y: -4 } : null))

function pupilT(force, natural) {
  const p = force || natural
  return `translate(${p.x}px, ${p.y}px)`
}

/* ── blink scheduling ── */
let pinkBT, darkBT

function scheduleBlink(flag, key) {
  const t = setTimeout(() => {
    flag.value = true
    setTimeout(() => {
      flag.value = false
      scheduleBlink(flag, key)
    }, 150)
  }, Math.random() * 4000 + 3000)
  if (key === 'p') pinkBT = t
  else darkBT = t
}

/* ── look at each other when typing starts ── */
let lookT
watch(
  () => props.isTyping,
  (v) => {
    if (lookT) clearTimeout(lookT)
    if (v) {
      lookingAtEach.value = true
      lookT = setTimeout(() => {
        lookingAtEach.value = false
      }, 800)
    } else {
      lookingAtEach.value = false
    }
  },
)

/* ── pink character sneaky peeking ── */
let peekT
watch([() => props.passwordLength, () => props.showPassword, pinkPeeking], () => {
  if (peekT) clearTimeout(peekT)
  if (props.passwordLength > 0 && props.showPassword && !pinkPeeking.value) {
    peekT = setTimeout(() => {
      pinkPeeking.value = true
      setTimeout(() => {
        pinkPeeking.value = false
      }, 800)
    }, Math.random() * 3000 + 2000)
  } else if (!(props.passwordLength > 0 && props.showPassword)) {
    pinkPeeking.value = false
  }
})

/* ── lifecycle ── */
onMounted(() => {
  window.addEventListener('mousemove', onMouse)
  scheduleBlink(pinkBlink, 'p')
  scheduleBlink(darkBlink, 'd')
})

onUnmounted(() => {
  window.removeEventListener('mousemove', onMouse)
  if (rafId) cancelAnimationFrame(rafId)
  ;[pinkBT, darkBT, lookT, peekT].forEach((t) => t && clearTimeout(t))
})
</script>

<template>
  <div class="relative mx-auto" style="width: 550px; height: 400px">
    <!-- ═══ Pink character (back, tall) ═══ -->
    <div
      ref="pinkEl"
      class="absolute bottom-0 transition-all duration-700 ease-in-out"
      :style="{
        left: '70px',
        width: '180px',
        height: isTyping || hiding ? '440px' : '400px',
        backgroundColor: C.primary,
        borderRadius: '10px 10px 0 0',
        zIndex: 1,
        transform: showing
          ? 'skewX(0deg)'
          : isTyping || hiding
            ? `skewX(${(pp.skew || 0) - 12}deg) translateX(40px)`
            : `skewX(${pp.skew || 0}deg)`,
        transformOrigin: 'bottom center',
      }"
    >
      <div
        class="absolute flex gap-8 transition-all duration-700 ease-in-out"
        :style="{
          left: showing ? '20px' : lookingAtEach ? '55px' : `${45 + pp.fx}px`,
          top: showing ? '35px' : lookingAtEach ? '65px' : `${40 + pp.fy}px`,
        }"
      >
        <div
          v-for="i in 2"
          :key="'pe' + i"
          class="flex items-center justify-center rounded-full transition-all duration-150 overflow-hidden"
          :style="{
            width: '18px',
            height: pinkBlink ? '2px' : '18px',
            backgroundColor: C.sclera,
          }"
        >
          <div
            v-if="!pinkBlink"
            class="rounded-full"
            :style="{
              width: '7px',
              height: '7px',
              backgroundColor: C.pupil,
              transform: pupilT(pinkForce, pPupil),
              transition: 'transform 0.1s ease-out',
            }"
          />
        </div>
      </div>
    </div>

    <!-- ═══ Neutral character (middle) ═══ -->
    <div
      ref="neutralEl"
      class="absolute bottom-0 transition-all duration-700 ease-in-out"
      :style="{
        left: '240px',
        width: '120px',
        height: '310px',
        backgroundColor: C.neutral,
        borderRadius: '8px 8px 0 0',
        zIndex: 2,
        transform: showing
          ? 'skewX(0deg)'
          : lookingAtEach
            ? `skewX(${(dp.skew || 0) * 1.5 + 10}deg) translateX(20px)`
            : isTyping || hiding
              ? `skewX(${(dp.skew || 0) * 1.5}deg)`
              : `skewX(${dp.skew || 0}deg)`,
        transformOrigin: 'bottom center',
      }"
    >
      <div
        class="absolute flex gap-6 transition-all duration-700 ease-in-out"
        :style="{
          left: showing ? '10px' : lookingAtEach ? '32px' : `${26 + dp.fx}px`,
          top: showing ? '28px' : lookingAtEach ? '12px' : `${32 + dp.fy}px`,
        }"
      >
        <div
          v-for="i in 2"
          :key="'de' + i"
          class="flex items-center justify-center rounded-full transition-all duration-150 overflow-hidden"
          :style="{
            width: '16px',
            height: darkBlink ? '2px' : '16px',
            backgroundColor: C.sclera,
          }"
        >
          <div
            v-if="!darkBlink"
            class="rounded-full"
            :style="{
              width: '6px',
              height: '6px',
              backgroundColor: C.pupil,
              transform: pupilT(darkForce, dPupil),
              transition: 'transform 0.1s ease-out',
            }"
          />
        </div>
      </div>
    </div>

    <!-- ═══ Warm character (front-left, semi-circle) ═══ -->
    <div
      ref="warmEl"
      class="absolute bottom-0 transition-all duration-700 ease-in-out"
      :style="{
        left: '0px',
        width: '240px',
        height: '200px',
        backgroundColor: C.warm,
        borderRadius: '120px 120px 0 0',
        zIndex: 3,
        transform: showing ? 'skewX(0deg)' : `skewX(${gp.skew || 0}deg)`,
        transformOrigin: 'bottom center',
      }"
    >
      <div
        class="absolute flex gap-8 transition-all duration-200 ease-out"
        :style="{
          left: showing ? '50px' : `${82 + (gp.fx || 0)}px`,
          top: showing ? '85px' : `${90 + (gp.fy || 0)}px`,
        }"
      >
        <div
          v-for="i in 2"
          :key="'gp' + i"
          class="rounded-full"
          :style="{
            width: '12px',
            height: '12px',
            backgroundColor: C.pupil,
            transform: pupilT(goldForce, gPupil),
            transition: 'transform 0.1s ease-out',
          }"
        />
      </div>
    </div>

    <!-- ═══ Info character (front-right, rounded rect + mouth) ═══ -->
    <div
      ref="infoEl"
      class="absolute bottom-0 transition-all duration-700 ease-in-out"
      :style="{
        left: '310px',
        width: '140px',
        height: '230px',
        backgroundColor: C.info,
        borderRadius: '70px 70px 0 0',
        zIndex: 4,
        transform: showing ? 'skewX(0deg)' : `skewX(${cp.skew || 0}deg)`,
        transformOrigin: 'bottom center',
      }"
    >
      <div
        class="absolute flex gap-6 transition-all duration-200 ease-out"
        :style="{
          left: showing ? '20px' : `${52 + (cp.fx || 0)}px`,
          top: showing ? '35px' : `${40 + (cp.fy || 0)}px`,
        }"
      >
        <div
          v-for="i in 2"
          :key="'cp' + i"
          class="rounded-full"
          :style="{
            width: '12px',
            height: '12px',
            backgroundColor: C.pupil,
            transform: pupilT(cyanForce, cPupil),
            transition: 'transform 0.1s ease-out',
          }"
        />
      </div>
      <!-- Mouth -->
      <div
        class="absolute rounded-full transition-all duration-200 ease-out"
        :style="{
          width: '80px',
          height: '4px',
          backgroundColor: C.pupil,
          left: showing ? '10px' : `${40 + (cp.fx || 0)}px`,
          top: showing ? '88px' : `${88 + (cp.fy || 0)}px`,
        }"
      />
    </div>
  </div>
</template>
