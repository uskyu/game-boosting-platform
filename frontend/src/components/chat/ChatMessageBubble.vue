<script setup>
import { computed, ref } from 'vue'
import { formatShortDate } from '@/utils/display'

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
  currentUserId: {
    type: [Number, String],
    default: null,
  },
  showReadReceipt: {
    type: Boolean,
    default: false,
  },
  canRecall: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['recall'])

const isSystem = computed(() => props.message.message_type === 'SYSTEM')
const isImage = computed(() => props.message.message_type === 'IMAGE')
const isRecalled = computed(() => Boolean(props.message.recalled_at))
const isSelf = computed(() => Number(props.message.sender_id) === Number(props.currentUserId))
const isAdmin = computed(() => props.message.sender?.role === 'ADMIN')

const senderName = computed(() => {
  if (isSystem.value) {
    return '系统'
  }

  if (isSelf.value) {
    return '你'
  }

  return props.message.sender?.username || '对方'
})

const displayTime = computed(() => formatShortDate(props.message.created_at))

const imageUrl = computed(() => {
  if (!isImage.value || !props.message.content) {
    return ''
  }

  return props.message.content
})

const isPreviewOpen = ref(false)

function handleRecall() {
  emit('recall', props.message.id)
}

function openPreview() {
  if (!imageUrl.value) {
    return
  }

  isPreviewOpen.value = true
}

function closePreview() {
  isPreviewOpen.value = false
}
</script>

<template>
  <div
    class="flex"
    :class="{
      'justify-center': isSystem || isRecalled,
      'justify-end': !isSystem && !isRecalled && isSelf,
      'justify-start': !isSystem && !isRecalled && !isSelf,
    }"
  >
    <div v-if="isSystem" class="chat-system-message">
      {{ message.content || '系统消息' }}
    </div>

    <div v-else-if="isRecalled" class="chat-system-message">
      {{ senderName }} 撤回了一条消息
    </div>

    <div
      v-else
      class="flex max-w-[85%] flex-col sm:max-w-[72%]"
      :class="isSelf ? 'items-end' : 'items-start'"
    >
      <div
        v-if="!isSelf"
        class="mb-2 flex items-center gap-2 text-xs text-ink-2"
      >
        <span>{{ senderName }}</span>
        <span
          v-if="isAdmin"
          class="rounded-full bg-warning-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-warning"
        >
          管理员
        </span>
      </div>

      <div
        class="chat-bubble"
        :class="{
          'chat-bubble-self': isSelf,
          'chat-bubble-other': !isSelf && !isAdmin,
          'chat-bubble-admin': !isSelf && isAdmin,
        }"
      >
        <button
          v-if="canRecall"
          type="button"
          class="chat-bubble-action"
          @click="handleRecall"
        >
          撤回
        </button>

        <button
          v-if="isImage"
          type="button"
          class="block overflow-hidden rounded-tile"
          @click="openPreview"
        >
          <img
            :src="imageUrl"
            alt="聊天图片"
            class="max-h-72 w-full object-cover transition duration-300 hover:scale-[1.02]"
          />
        </button>

        <p
          v-else
          class="whitespace-pre-wrap break-words text-sm leading-7"
        >
          {{ message.content }}
        </p>
      </div>

      <div
        class="mt-2 flex items-center gap-2 text-[11px] text-ink-3"
        :class="isSelf ? 'justify-end' : 'justify-start'"
      >
        <span>{{ displayTime }}</span>
        <span v-if="showReadReceipt && isSelf" class="text-primary">已读</span>
      </div>
    </div>
  </div>

  <teleport to="body">
    <div
      v-if="isPreviewOpen"
      class="modal-scrim !z-[120]"
      @click.self="closePreview"
    >
      <button
        type="button"
        class="absolute right-6 top-6 rounded-full border border-line-2 bg-elevated px-4 py-2 text-sm text-ink-1 transition hover:border-line-2 hover:text-primary"
        @click="closePreview"
      >
        关闭
      </button>
      <img
        :src="imageUrl"
        alt="聊天图片预览"
        class="max-h-full max-w-full rounded-card border border-line-1 object-contain"
      />
    </div>
  </teleport>
</template>
