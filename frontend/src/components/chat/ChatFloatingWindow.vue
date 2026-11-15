<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatPanel from './ChatPanel.vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false,
  },
  conversationId: {
    type: [Number, String],
    default: null,
  },
  title: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['close', 'expand'])

const DESKTOP_QUERY = '(min-width: 1280px)'

function readDesktop() {
  if (typeof window === 'undefined') {
    return true
  }

  if (typeof window.matchMedia === 'function') {
    return window.matchMedia(DESKTOP_QUERY).matches
  }

  return window.innerWidth >= 1280
}

const dialogRef = ref(null)
const scrimRef = ref(null)
const isDesktop = ref(readDesktop())
const viewportStyle = ref({})

let mediaQuery = null
let previousFocus = null
let viewportListening = false

function isVisible() {
  return Boolean(props.open && props.conversationId)
}

function requestClose() {
  if (!props.open) {
    return
  }

  emit('close')
}

function focusDialog() {
  nextTick(() => {
    const closeButton = dialogRef.value?.querySelector('[data-chat-close]')
    const target = closeButton || dialogRef.value
    if (!target || typeof target.focus !== 'function') {
      return
    }

    try {
      target.focus({ preventScroll: true })
    } catch {
      target.focus()
    }
  })
}

function restoreFocus() {
  const target = previousFocus
  previousFocus = null

  if (!target || !target.isConnected || typeof target.focus !== 'function') {
    return
  }

  try {
    target.focus({ preventScroll: true })
  } catch {
    target.focus()
  }
}

function hasBlockingOverlay() {
  // 聊天图片预览（z-120 modal-scrim）或订单图片大图（.lb-overlay）打开时，
  // ESC 应优先由它们消费，不要连带把聊天窗一起关掉。
  const overlays = document.querySelectorAll('.modal-scrim, .lb-overlay')

  return Array.from(overlays).some((element) => element !== scrimRef.value)
}

function onKeydown(event) {
  if (event.key !== 'Escape' || !props.open) {
    return
  }

  if (hasBlockingOverlay()) {
    return
  }

  requestClose()
}

function syncViewport() {
  if (
    typeof window === 'undefined'
    || !window.visualViewport
    || isDesktop.value
    || !props.open
  ) {
    viewportStyle.value = {}
    return
  }

  const viewport = window.visualViewport
  const layoutHeight = window.innerHeight || document.documentElement.clientHeight
  // iOS Safari 键盘不改变布局视口，只缩小视觉视口：用 bottom 偏移把抽屉顶到键盘上方；
  // Android Chrome resize-content 时布局视口同步缩小，走 CSS dvh 即可。
  const keyboardInset = Math.max(0, layoutHeight - viewport.height - viewport.offsetTop)

  if (keyboardInset < 80) {
    viewportStyle.value = {}
    return
  }

  const availableHeight = Math.max(240, Math.floor(viewport.height - 12))
  viewportStyle.value = {
    bottom: `${Math.round(keyboardInset)}px`,
    height: `${availableHeight}px`,
    maxHeight: `${availableHeight}px`,
  }
}

function startViewportSync() {
  viewportStyle.value = {}

  if (isDesktop.value || typeof window === 'undefined') {
    return
  }

  if (!window.visualViewport || viewportListening) {
    syncViewport()
    return
  }

  viewportListening = true
  window.visualViewport.addEventListener('resize', syncViewport)
  window.visualViewport.addEventListener('scroll', syncViewport)
  window.addEventListener('resize', syncViewport)
  syncViewport()
}

function stopViewportSync() {
  viewportStyle.value = {}

  if (!viewportListening) {
    return
  }

  viewportListening = false
  window.visualViewport.removeEventListener('resize', syncViewport)
  window.visualViewport.removeEventListener('scroll', syncViewport)
  window.removeEventListener('resize', syncViewport)
}

function handleOpened() {
  previousFocus = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  document.addEventListener('keydown', onKeydown)
  startViewportSync()
  focusDialog()
}

function handleClosed() {
  document.removeEventListener('keydown', onKeydown)
  stopViewportSync()
  restoreFocus()
}

function handleDesktopChange(event) {
  isDesktop.value = event.matches

  if (!props.open) {
    return
  }

  if (isDesktop.value) {
    stopViewportSync()
  } else {
    startViewportSync()
  }

  focusDialog()
}

function setupMediaListener() {
  if (typeof window === 'undefined' || !window.matchMedia) {
    isDesktop.value = readDesktop()
    return
  }

  mediaQuery = window.matchMedia(DESKTOP_QUERY)
  isDesktop.value = mediaQuery.matches

  if (mediaQuery.addEventListener) {
    mediaQuery.addEventListener('change', handleDesktopChange)
  } else if (mediaQuery.addListener) {
    mediaQuery.addListener(handleDesktopChange)
  }
}

function teardownMediaListener() {
  if (!mediaQuery) {
    return
  }

  if (mediaQuery.removeEventListener) {
    mediaQuery.removeEventListener('change', handleDesktopChange)
  } else if (mediaQuery.removeListener) {
    mediaQuery.removeListener(handleDesktopChange)
  }

  mediaQuery = null
}

watch(
  () => Boolean(props.open && props.conversationId),
  (visible, wasVisible) => {
    if (visible) {
      handleOpened()
    } else if (wasVisible) {
      handleClosed()
    }
  }
)

onMounted(() => {
  setupMediaListener()

  if (isVisible()) {
    handleOpened()
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  stopViewportSync()
  teardownMediaListener()
  restoreFocus()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open && conversationId" class="chat-float-root">
      <div
        v-if="!isDesktop"
        ref="scrimRef"
        class="modal-scrim chat-float-scrim"
        role="presentation"
        @click.self="requestClose"
      ></div>

      <section
        ref="dialogRef"
        class="chat-float-card surface-card"
        :class="{
          'chat-float-card--desktop': isDesktop,
          'chat-float-card--sheet': !isDesktop,
        }"
        role="dialog"
        :aria-modal="isDesktop ? 'false' : 'true'"
        :aria-label="title || '聊天窗口'"
        tabindex="-1"
        :style="viewportStyle"
      >
        <ChatPanel
          :conversation-id="conversationId"
          compact
          floating
          show-close
          @close="requestClose"
          @expand="emit('expand')"
        />
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.chat-float-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--elevated);
  border: 1px solid var(--line-1);
  box-shadow: var(--shadow-pop);
  z-index: 61;
  outline: none;
}

/* 桌面 >=1280px：右侧非模态浮动卡片，不铺遮罩、不锁页面滚动 */
.chat-float-card--desktop {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: min(420px, calc(100vw - 32px));
  /* 老内核不认 min()/dvh：先给纯 vh/px 兜底，再逐级覆盖 */
  height: 640px;
  max-height: calc(100vh - 120px);
  height: min(640px, calc(100vh - 120px));
  height: min(640px, calc(100dvh - 120px));
  max-height: calc(100dvh - 120px);
  border-radius: 22px;
  animation: chatFloatIn 0.18s ease-out both;
}

/* 手机/平板 <1280px：底部抽屉，键盘弹起时由 visualViewport 的 inline style 接管高度 */
.chat-float-card--sheet {
  position: fixed;
  top: auto;
  right: 0;
  bottom: 0;
  left: 0;
  height: 88vh;
  max-height: calc(100vh - 12px);
  height: min(88vh, calc(100vh - 12px));
  height: min(88dvh, calc(100dvh - 12px));
  max-height: calc(100dvh - 12px);
  padding-bottom: 0;
  padding-bottom: env(safe-area-inset-bottom);
  border-radius: 22px 22px 0 0;
  border-bottom: none;
  animation: chatFloatSheetIn 0.2s ease-out both;
}

.chat-float-scrim {
  position: fixed;
  /* 老 Android X5 不认 inset 简写：物理四边兜底，再写 inset */
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-end;
  padding: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  touch-action: none;
  overscroll-behavior: contain;
}

@keyframes chatFloatIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes chatFloatSheetIn {
  from {
    opacity: 0;
    transform: translateY(24px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .chat-float-card--desktop,
  .chat-float-card--sheet {
    animation: none;
  }
}
</style>
