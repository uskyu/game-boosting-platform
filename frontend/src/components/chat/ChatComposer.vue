<script setup>
import { ref } from 'vue'
import {
  CHAT_IMAGE_ACCEPT,
  chatImageFilesFromClipboard,
  validateChatImageFiles,
} from '@/utils/chatImage'

const props = defineProps({
  modelValue: {
    type: String,
    default: '',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  sending: {
    type: Boolean,
    default: false,
  },
  // 悬浮小窗模式：隐藏安全提示、缩短 placeholder，避免窄宽度下输入框
  // 占位文字换行后被裁切；完整聊天页保持原有提示。
  compact: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'send', 'send-images', 'image-error', 'typing'])

const fileInputRef = ref(null)

function updateValue(value) {
  emit('update:modelValue', value)
}

function handleInput(event) {
  updateValue(event.target.value)
  emit('typing')
}

function handleSubmit() {
  emit('send')
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSubmit()
  }
}

function openFileDialog() {
  if (props.disabled || props.sending) {
    return
  }

  fileInputRef.value?.click()
}

function submitImages(files) {
  if (props.disabled || props.sending || !files.length) {
    return
  }

  const error = validateChatImageFiles(files)
  if (error) {
    emit('image-error', error)
    return
  }

  emit('send-images', files)
}

function handleFileChange(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  submitImages(files)
}

function handlePaste(event) {
  const files = chatImageFilesFromClipboard(event.clipboardData)
  if (!files.length) {
    // 纯文本粘贴保持浏览器默认行为；只有剪贴板里带图片时才接管。
    return
  }

  event.preventDefault()
  submitImages(files)
}
</script>

<template>
  <div class="chat-composer">
    <div v-if="!compact" class="chat-security-notice">
      请勿在聊天中发送账号密码等敏感信息
    </div>

    <div class="flex items-center gap-2 sm:gap-3">
      <input
        ref="fileInputRef"
        type="file"
        class="hidden"
        :accept="CHAT_IMAGE_ACCEPT"
        multiple
        @change="handleFileChange"
      />

      <button
        type="button"
        class="chat-icon-button"
        :disabled="disabled || sending"
        title="发送图片（支持拖拽或粘贴）"
        aria-label="发送图片，支持拖拽或粘贴"
        @click="openFileDialog"
      >
        图片
      </button>

      <label class="flex-1">
        <span class="sr-only">输入消息</span>
        <textarea
          :value="modelValue"
          rows="1"
          class="chat-textarea"
          :disabled="disabled || sending"
          :placeholder="compact ? '输入消息…' : '输入聊天内容，Enter 发送，Shift + Enter 换行'"
          @input="handleInput"
          @keydown="handleKeydown"
          @paste="handlePaste"
        ></textarea>
      </label>

      <button
        type="button"
        class="btn-primary h-11 min-w-16 justify-center !py-0 sm:min-w-28"
        :disabled="disabled || sending || !modelValue.trim()"
        @click="handleSubmit"
      >
        {{ sending ? '发送中...' : '发送' }}
      </button>
    </div>
  </div>
</template>
