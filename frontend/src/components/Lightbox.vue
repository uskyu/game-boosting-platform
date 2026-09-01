<template>
  <Teleport to="body">
    <Transition name="lb-fade">
      <div v-if="visible && count > 0" class="lb-overlay" @click.self="close">
        <button type="button" class="lb-close" aria-label="关闭预览" @click="close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>

        <button v-if="count > 1" type="button" class="lb-nav lb-prev" aria-label="上一张" @click="prev">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>

        <img :src="current.url" :alt="current.name || '订单图片'" class="lb-image" draggable="false" @click="next" />

        <button v-if="count > 1" type="button" class="lb-nav lb-next" aria-label="下一张" @click="next">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>

        <div class="lb-caption">
          <span v-if="current.name" class="lb-name">{{ current.name }}</span>
          <span v-if="count > 1" class="lb-index">{{ index + 1 }} / {{ count }}</span>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  // 图片列表：字符串 url 或 { url, name }
  images: { type: Array, default: () => [] },
  visible: { type: Boolean, default: false },
  startIndex: { type: Number, default: 0 },
})

const emit = defineEmits(['close'])

const normalized = computed(() =>
  (props.images || [])
    .map((item) => (typeof item === 'string' ? { url: item } : item))
    .filter((item) => item && item.url)
)

const count = computed(() => normalized.value.length)
const index = ref(0)

const current = computed(() => normalized.value[index.value] || { url: '' })

function clamp(value) {
  if (count.value === 0) return 0
  return Math.min(Math.max(value, 0), count.value - 1)
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      index.value = clamp(props.startIndex)
      document.addEventListener('keydown', onKeydown)
      document.body.style.overflow = 'hidden'
    } else {
      cleanup()
    }
  }
)

watch(
  () => props.startIndex,
  (value) => {
    if (props.visible) index.value = clamp(value)
  }
)

function prev() {
  if (count.value === 0) return
  index.value = (index.value - 1 + count.value) % count.value
}

function next() {
  if (count.value === 0) return
  index.value = (index.value + 1) % count.value
}

function close() {
  emit('close')
}

function onKeydown(event) {
  if (event.key === 'Escape') close()
  else if (event.key === 'ArrowLeft') prev()
  else if (event.key === 'ArrowRight') next()
}

function cleanup() {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
}

onBeforeUnmount(cleanup)
</script>

<style scoped>
.lb-overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.9);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  animation: lb-in 0.18s ease-out;
}

.lb-image {
  max-width: min(92vw, 1200px);
  max-height: 86vh;
  object-fit: contain;
  border-radius: 14px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  user-select: none;
  cursor: zoom-in;
}

.lb-close,
.lb-nav {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 9999px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  cursor: pointer;
  transition: background 0.15s ease, transform 0.15s ease;
}

.lb-close:hover,
.lb-nav:hover {
  background: rgba(255, 255, 255, 0.22);
}

.lb-close:active,
.lb-nav:active {
  transform: scale(0.94);
}

.lb-close svg,
.lb-nav svg {
  width: 20px;
  height: 20px;
}

.lb-close {
  top: max(16px, env(safe-area-inset-top));
  right: 16px;
}

.lb-nav {
  top: 50%;
  transform: translateY(-50%);
}

.lb-prev {
  left: 16px;
}

.lb-next {
  right: 16px;
}

.lb-caption {
  position: absolute;
  bottom: max(16px, env(safe-area-inset-bottom));
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: rgba(255, 255, 255, 0.82);
  font-size: 13px;
}

.lb-name {
  max-width: 60vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lb-index {
  padding: 2px 10px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.14);
  font-variant-numeric: tabular-nums;
}

.lb-fade-enter-active,
.lb-fade-leave-active {
  transition: opacity 0.18s ease;
}

.lb-fade-enter-from,
.lb-fade-leave-to {
  opacity: 0;
}

@keyframes lb-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .lb-prev {
    left: 8px;
  }

  .lb-next {
    right: 8px;
  }

  .lb-image {
    max-width: 96vw;
    max-height: 80vh;
  }
}
</style>
