<script setup>
import { ref } from 'vue'

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
})

const emit = defineEmits(['update:modelValue', 'send', 'send-image', 'typing'])

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

function handleFileChange(event) {
  const file = event.target.files?.[0]
  if (file) {
    emit('send-image', file)
  }

  event.target.value = ''
}
</script>

<template>
  <div class="chat-composer">
    <div class="chat-security-notice">
      请勿在聊天中发送账号密码等敏感信息
    </div>

    <div class="flex items-end gap-3">
      <input
        ref="fileInputRef"
        type="file"
        class="hidden"
        accept="image/png,image/jpeg,image/gif,image/webp"
        @change="handleFileChange"
      />

      <button
        type="button"
        class="chat-icon-button"
        :disabled="disabled || sending"
        @click="openFileDialog"
      >
        图片
      </button>

      <label class="flex-1">
        <span class="sr-only">输入消息</span>
        <textarea
          :value="modelValue"
          rows="3"
          class="chat-textarea"
          :disabled="disabled || sending"
          placeholder="输入聊天内容，Enter 发送，Shift + Enter 换行"
          @input="handleInput"
          @keydown="handleKeydown"
        ></textarea>
      </label>

      <button
        type="button"
        class="btn-primary min-w-28 justify-center py-3"
        :disabled="disabled || sending || !modelValue.trim()"
        @click="handleSubmit"
      >
        {{ sending ? '发送中...' : '发送' }}
      </button>
    </div>
  </div>
</template>
