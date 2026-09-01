<script setup>
import { computed, ref, watch } from 'vue'
import { useOrdersStore } from '@/stores/orders'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  orderId: { type: [String, Number], required: true },
})

const emit = defineEmits(['update:modelValue', 'success'])

const ordersStore = useOrdersStore()
const note = ref('')
const files = ref([]) // File[]
const previews = ref([]) // object urls
const uploadStates = ref([]) // 'idle' | 'uploading' | 'done' | 'error'
const uploadErrors = ref([]) // string per file
const submitting = ref(false)
const generalError = ref('')

const noteLen = computed(() => note.value.length)
const canSubmit = computed(() => !submitting.value && noteLen.value <= 2000)

const MAX_FILES = 5
const MAX_SIZE = 5 * 1024 * 1024
const ALLOWED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp'])

function reset() {
  note.value = ''
  clearFiles()
  submitting.value = false
  generalError.value = ''
}

function clearFiles() {
  previews.value.forEach((u) => { try { URL.revokeObjectURL(u) } catch {} })
  files.value = []
  previews.value = []
  uploadStates.value = []
  uploadErrors.value = []
}

watch(() => props.modelValue, (open) => {
  if (!open) {
    submitting.value = false
    generalError.value = ''
  }
})

function onClose() {
  if (submitting.value) return
  emit('update:modelValue', false)
}

function validateFile(file) {
  if (!ALLOWED_TYPES.has(file.type)) return '仅支持 png / jpeg / webp'
  if (file.size > MAX_SIZE) return '单张不能超过 5MB'
  if (file.size <= 0) return '文件为空'
  return ''
}

function onPick(e) {
  generalError.value = ''
  const list = Array.from(e.target.files || [])
  if (!list.length) return
  const remaining = MAX_FILES - files.value.length
  if (remaining <= 0) {
    generalError.value = `最多 ${MAX_FILES} 张`
    e.target.value = ''
    return
  }
  const toAdd = list.slice(0, remaining)
  for (const f of toAdd) {
    const err = validateFile(f)
    if (err) {
      generalError.value = `${f.name}: ${err}`
      continue
    }
    files.value.push(f)
    previews.value.push(URL.createObjectURL(f))
    uploadStates.value.push('idle')
    uploadErrors.value.push('')
  }
  if (list.length > remaining) {
    generalError.value = `最多 ${MAX_FILES} 张，已截取前 ${remaining} 张`
  }
  e.target.value = ''
}

function removeAt(idx) {
  try { URL.revokeObjectURL(previews.value[idx]) } catch {}
  files.value.splice(idx, 1)
  previews.value.splice(idx, 1)
  uploadStates.value.splice(idx, 1)
  uploadErrors.value.splice(idx, 1)
}

async function uploadOne(idx) {
  const file = files.value[idx]
  if (!file) return { success: true }
  uploadStates.value[idx] = 'uploading'
  uploadErrors.value[idx] = ''
  const res = await ordersStore.uploadDeliverAttachment(props.orderId, file)
  if (res.success) {
    uploadStates.value[idx] = 'done'
    return { success: true }
  }
  uploadStates.value[idx] = 'error'
  uploadErrors.value[idx] = res.error || '上传失败'
  return { success: false, error: res.error }
}

async function retryOne(idx) {
  await uploadOne(idx)
}

async function handleSubmit() {
  generalError.value = ''
  if (noteLen.value > 2000) {
    generalError.value = '交付说明不能超过 2000 字'
    return
  }
  if (files.value.length > MAX_FILES) {
    generalError.value = `最多 ${MAX_FILES} 张`
    return
  }
  submitting.value = true
  // 先逐张上传 deliver-attachments
  for (let i = 0; i < files.value.length; i++) {
    if (uploadStates.value[i] === 'done') continue
    const r = await uploadOne(i)
    if (!r.success) {
      generalError.value = `第 ${i + 1} 张上传失败，可重试；失败后不会提交完成`
      submitting.value = false
      return
    }
  }
  // 再调 deliver
  const res = await ordersStore.deliverOrder(props.orderId, note.value.trim())
  if (!res.success) {
    generalError.value = res.error || '提交失败'
    submitting.value = false
    return
  }
  submitting.value = false
  emit('success', res.data)
  emit('update:modelValue', false)
  reset()
}
</script>

<template>
  <teleport to="body">
    <div v-if="modelValue" class="modal-scrim modal-scrim--sheet" @click.self="onClose">
      <div class="modal-card modal-sheet !max-w-[560px]" role="dialog" aria-modal="true" aria-label="提交完成">
        <div class="flex items-center justify-between gap-3">
          <h3 class="text-lg font-semibold text-ink-1">提交完成</h3>
          <button type="button" class="btn-ghost !min-h-[44px] !px-3" :disabled="submitting" @click="onClose">关闭</button>
        </div>

        <div v-if="generalError" class="message-error mt-3">{{ generalError }}</div>

        <div class="mt-4 space-y-4">
          <div>
            <label class="label" for="deliver-note">交付说明（可选，最多 2000 字）</label>
            <textarea
              id="deliver-note"
              v-model="note"
              class="input min-h-[96px] resize-y"
              rows="4"
              maxlength="2000"
              placeholder="例如：已完成目标段位，附截图…"
            ></textarea>
            <p class="helper-text flex justify-between gap-2">
              <span>将随交付记录展示给老板</span>
              <span :class="noteLen > 2000 ? 'text-danger' : 'text-ink-3'">{{ noteLen }}/2000</span>
            </p>
          </div>

          <div>
            <label class="label">交付截图（可选，最多 5 张，png/jpeg/webp，单张 ≤5MB）</label>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              class="block w-full text-sm text-ink-2 file:mr-3 file:rounded-full file:border-0 file:bg-surface-3 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-ink-1 hover:file:bg-[var(--surface-3-hover)]"
              :disabled="submitting || files.length >= MAX_FILES"
              @change="onPick"
            />
            <p class="helper-text">先逐张上传到交付附件，再提交完成；失败可重试。</p>

            <div v-if="files.length" class="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-5">
              <div v-for="(url, idx) in previews" :key="idx" class="relative overflow-hidden rounded-tile border border-line-1 bg-surface-2">
                <img :src="url" :alt="files[idx]?.name || ''" class="h-20 w-full object-cover" />
                <div class="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-white/80 px-1 py-1 text-[10px] backdrop-blur dark:bg-black/40">
                  <span class="truncate text-ink-2">{{ uploadStates[idx] === 'done' ? '已上传' : uploadStates[idx] === 'uploading' ? '上传中…' : uploadStates[idx] === 'error' ? '失败' : '待上传' }}</span>
                  <button
                    v-if="uploadStates[idx] === 'error'"
                    type="button"
                    class="rounded-full bg-danger px-2 py-1 font-semibold text-on-primary"
                    :disabled="submitting"
                    @click="retryOne(idx)"
                  >重试</button>
                </div>
                <button
                  type="button"
                  class="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-xs text-white"
                  :disabled="submitting && uploadStates[idx] === 'uploading'"
                  aria-label="移除"
                  @click="removeAt(idx)"
                >×</button>
                <p v-if="uploadErrors[idx]" class="px-1 py-1 text-[10px] leading-3 text-danger">{{ uploadErrors[idx] }}</p>
              </div>
            </div>
          </div>
        </div>

        <div class="mt-6 flex gap-3">
          <button type="button" class="btn-secondary flex-1" :disabled="submitting" @click="onClose">取消</button>
          <button type="button" class="btn-success flex-1" :disabled="!canSubmit" @click="handleSubmit">{{ submitting ? '提交中…' : '确认提交' }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>
