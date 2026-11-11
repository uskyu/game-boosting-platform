<script setup>
import { ref, watch } from 'vue'

import { BLACKLIST_BLOCK_NOTICE, useBlacklistStore } from '@/stores/blacklist'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  userId: { type: [String, Number], default: null },
  username: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'blocked'])

const blacklistStore = useBlacklistStore()
const submitting = ref(false)
const error = ref('')

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    submitting.value = false
    error.value = ''
  },
)

function close() {
  if (submitting.value) return
  emit('update:modelValue', false)
}

async function submit() {
  if (submitting.value || props.userId == null) return
  submitting.value = true
  error.value = ''
  const result = await blacklistStore.add(props.userId)
  submitting.value = false
  if (result.success) {
    emit('blocked', Number(props.userId))
    emit('update:modelValue', false)
  } else {
    error.value = result.error || '拉黑失败，请稍后重试'
  }
}
</script>

<template>
  <teleport to="body">
    <div v-if="modelValue" class="modal-scrim" @click.self="close">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="拉黑该打手">
        <h3 class="text-lg font-semibold text-ink-1">拉黑该打手</h3>
        <p class="mt-3 text-sm leading-6 text-ink-2">
          将拉黑 <span class="font-semibold text-ink-1">{{ username || '该用户' }}</span>。
        </p>
        <p class="message-warning mt-3 text-xs leading-6">{{ BLACKLIST_BLOCK_NOTICE }}</p>
        <p v-if="error" class="message-error mt-3 text-sm">{{ error }}</p>
        <div class="mt-6 flex gap-3">
          <button type="button" class="btn-secondary flex-1" :disabled="submitting" @click="close">取消</button>
          <button type="button" class="btn-danger flex-1" :disabled="submitting" @click="submit">
            {{ submitting ? '提交中…' : '确认拉黑' }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>
