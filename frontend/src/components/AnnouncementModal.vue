<script setup>
import { onBeforeUnmount, onMounted } from 'vue'

defineProps({
  announcement: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['close'])

function close() {
  emit('close')
}

function handleKeydown(event) {
  if (event.key === 'Escape') close()
}

onMounted(() => document.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', handleKeydown))
</script>

<template>
  <teleport to="body">
    <div class="modal-scrim" role="presentation" @click.self="close">
      <section
        class="modal-card !max-w-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="announcement-title"
      >
        <div class="flex items-start justify-between gap-4">
          <div>
            <p class="eyebrow">平台公告</p>
            <h2 id="announcement-title" class="mt-2 text-2xl font-semibold text-ink-1">
              {{ announcement.title }}
            </h2>
          </div>
          <button
            type="button"
            class="btn-ghost !h-10 !w-10 !p-0"
            aria-label="关闭公告"
            @click="close"
          >
            <svg viewBox="0 0 24 24" class="mx-auto h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
            </svg>
          </button>
        </div>

        <div class="announcement-content mt-6" v-html="announcement.content_html"></div>

        <div class="mt-7 flex justify-end border-t border-line-1 pt-5">
          <button type="button" class="btn-primary min-h-[44px] !px-8" @click="close">我知道了</button>
        </div>
      </section>
    </div>
  </teleport>
</template>
