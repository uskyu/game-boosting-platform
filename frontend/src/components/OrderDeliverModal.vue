<script setup>
import { computed, ref, watch } from 'vue'
import { useOrdersStore } from '@/stores/orders'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  orderId: { type: [String, Number], required: true },
  // 老板开关：本单必须上传 ≥1 张完成截图才能结单
  requireDeliveryImage: { type: Boolean, default: false },
  // 名额里已保存的交付图数量（此前上传成功但未提交结单的图片）
  attachedCount: { type: Number, default: 0 },
  // 可选：已保存附件明细。传入时它是数量的权威来源，可用于显示真实预览。
  existingAttachments: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue', 'success', 'delete-existing'])

const ordersStore = useOrdersStore()
const note = ref('')
const files = ref([]) // File[]，只保存本次待上传/失败项
const previews = ref([]) // object urls
const uploadStates = ref([]) // 'idle' | 'uploading' | 'error'
const uploadErrors = ref([]) // string per file
const submitting = ref(false)
const generalError = ref('')

const MAX_FILES = 5
const MAX_SIZE = 10 * 1024 * 1024

const noteLen = computed(() => note.value.length)
const serverAttachmentCount = computed(() => {
  // Prefer concrete server entries; otherwise retain compatibility with callers that only pass a count.
  if (props.existingAttachments.length > 0) return props.existingAttachments.length
  return Math.max(0, Number(props.attachedCount) || 0)
})
// 附件上传成功后订单 store 会立即持久化，但 attachedCount 可能因 my_claim 未同步而暂时不变。
// 持有本弹窗已知的持久化数量下限，并在 prop 追上时取 max，避免本地成功和 prop 双计。
const persistedCountFloor = ref(serverAttachmentCount.value)
const lastServerAttachmentCount = ref(serverAttachmentCount.value)
watch(serverAttachmentCount, (count) => {
  if (count < lastServerAttachmentCount.value) {
    // 父级确认附件被删除后，允许重新释放上传名额。
    persistedCountFloor.value = count
  } else if (count > lastServerAttachmentCount.value) {
    persistedCountFloor.value = Math.max(persistedCountFloor.value, count)
  }
  lastServerAttachmentCount.value = count
})
watch(() => props.orderId, () => {
  persistedCountFloor.value = serverAttachmentCount.value
  lastServerAttachmentCount.value = serverAttachmentCount.value
})

const persistedCount = computed(() => Math.max(serverAttachmentCount.value, persistedCountFloor.value))
const queuedCount = computed(() => files.value.length)
const effectiveCount = computed(() => persistedCount.value + queuedCount.value)
const remainingSlots = computed(() => Math.max(0, MAX_FILES - effectiveCount.value))
// 门禁注意：上传是在点击「提交」时才发生的（onPick 只入队不触发上传），
// 所以按「已保存/本次已选择」判断，避免提交按钮与上传互相等待。
const needImage = computed(
  () => props.requireDeliveryImage && effectiveCount.value === 0
)
const canSubmit = computed(() =>
  !submitting.value && noteLen.value <= 2000 && !needImage.value && effectiveCount.value <= MAX_FILES
)

function clearFiles() {
  previews.value.forEach((url) => { try { URL.revokeObjectURL(url) } catch {} })
  files.value = []
  previews.value = []
  uploadStates.value = []
  uploadErrors.value = []
}

function reset() {
  note.value = ''
  clearFiles()
  submitting.value = false
  generalError.value = ''
}

watch(() => props.modelValue, (open) => {
  if (!open && !submitting.value) {
    // 清除未提交队列/错误，但保留本次成功上传的计数下限，直到父级附件数据追上。
    reset()
  }
}, { immediate: true })

watch(() => props.orderId, () => {
  reset()
  persistedCountFloor.value = serverAttachmentCount.value
  lastServerAttachmentCount.value = serverAttachmentCount.value
})

function onClose() {
  if (submitting.value) return
  emit('update:modelValue', false)
}

function validateFile(file) {
  if (file.size > MAX_SIZE) return '单张不能超过 10MB'
  if (file.size <= 0) return '文件为空'
  return ''
}

function onPick(event) {
  generalError.value = ''
  const list = Array.from(event.target.files || [])
  if (!list.length) return

  const remaining = remainingSlots.value
  if (remaining <= 0) {
    generalError.value = `当前已有 ${effectiveCount.value} 张，最多还能上传 ${remaining} 张。请先移除本次未上传图片，或删除已保存草稿附件。`
    event.target.value = ''
    return
  }

  const toAdd = list.slice(0, remaining)
  for (const file of toAdd) {
    const error = validateFile(file)
    if (error) {
      generalError.value = `${file.name}: ${error}`
      continue
    }
    files.value.push(file)
    previews.value.push(URL.createObjectURL(file))
    uploadStates.value.push('idle')
    uploadErrors.value.push('')
  }
  if (list.length > remaining) {
    generalError.value = `最多 5 张，已只保留本次可上传的 ${remaining} 张；当前待上传 ${files.value.length} 张，共计 ${effectiveCount.value} 张。`
  }
  event.target.value = ''
}

function removeAt(index) {
  if (submitting.value) return
  try { URL.revokeObjectURL(previews.value[index]) } catch {}
  files.value.splice(index, 1)
  previews.value.splice(index, 1)
  uploadStates.value.splice(index, 1)
  uploadErrors.value.splice(index, 1)
  generalError.value = ''
}

function isAttachmentLimitError(error) {
  const message = String(error || '').toLowerCase()
  return message.includes('每单最多上传5张交付附件')
    || /(?:too many|max(?:imum)?).*(?:file|attachment)|(?:file|attachment).*(?:limit|maximum|exceed)/i.test(message)
}

function attachmentLimitMessage() {
  if (props.existingAttachments.length) {
    return '服务器已有 5 张，已达到上限；请先删除已保存草稿附件后重试。可通过下方删除按钮解除限制。'
  }
  return '服务器已有 5 张，已达到上限；请刷新详情同步附件后，删除已保存草稿附件再重试。'
}

async function uploadOne(file) {
  const index = files.value.indexOf(file)
  if (index < 0) return { success: true }

  const persistedBeforeUpload = persistedCount.value
  uploadStates.value[index] = 'uploading'
  uploadErrors.value[index] = ''
  const result = await ordersStore.uploadDeliverAttachment(props.orderId, file)
  const currentIndex = files.value.indexOf(file)
  if (result.success) {
    // prop 可能在请求返回前已更新；取请求前数量 +1 与当前服务端数量的较大值，避免成功项双计。
    persistedCountFloor.value = Math.max(serverAttachmentCount.value, persistedBeforeUpload + 1)
    if (currentIndex >= 0) {
      try { URL.revokeObjectURL(previews.value[currentIndex]) } catch {}
      files.value.splice(currentIndex, 1)
      previews.value.splice(currentIndex, 1)
      uploadStates.value.splice(currentIndex, 1)
      uploadErrors.value.splice(currentIndex, 1)
    }
    return { success: true }
  }

  const message = isAttachmentLimitError(result.error)
    ? attachmentLimitMessage()
    : (result.error || '上传失败')
  if (currentIndex >= 0) {
    uploadStates.value[currentIndex] = 'error'
    uploadErrors.value[currentIndex] = message
  }
  return { success: false, error: message }
}

async function retryOne(index) {
  if (submitting.value) return
  const file = files.value[index]
  if (!file) return
  submitting.value = true
  try {
    const result = await uploadOne(file)
    generalError.value = result.success ? '' : `第 ${index + 1} 张上传失败：${result.error || '请稍后重试'}`
  } catch (error) {
    const currentIndex = files.value.indexOf(file)
    const message = error?.message || '上传失败'
    if (currentIndex >= 0) {
      uploadStates.value[currentIndex] = 'error'
      uploadErrors.value[currentIndex] = message
    }
    generalError.value = `第 ${index + 1} 张上传失败：${message}`
  } finally {
    submitting.value = false
  }
}

function deleteExistingAt(index) {
  if (submitting.value) return
  const attachment = props.existingAttachments?.[index]
  if (!attachment) return
  emit('delete-existing', index, attachment)
}

async function handleSubmit() {
  if (submitting.value) return
  generalError.value = ''
  if (noteLen.value > 2000) {
    generalError.value = '汇报说明不能超过 2000 字'
    return
  }
  if (effectiveCount.value > MAX_FILES) {
    generalError.value = `当前已有 ${effectiveCount.value} 张，最多 5 张。请先删除已保存草稿附件或移除本次未上传图片。`
    return
  }
  if (needImage.value) {
    generalError.value = '该订单要求至少上传 1 张完成截图后才能提交结单'
    return
  }

  submitting.value = true
  try {
    // 快照本次队列；上传成功后会从队列移除，失败项保留以便删除/重试。
    for (const file of [...files.value]) {
      const result = await uploadOne(file)
      if (!result.success) {
        const index = files.value.indexOf(file)
        generalError.value = `第 ${index >= 0 ? index + 1 : 1} 张上传失败：${result.error || '请稍后重试'}。已上传的图片会保留，订单尚未结束。`
        return
      }
    }

    const result = await ordersStore.deliverOrder(props.orderId, note.value.trim())
    if (!result.success) {
      generalError.value = result.error || '提交失败'
      return
    }
    emit('success', result.data)
    emit('update:modelValue', false)
    reset()
  } catch (error) {
    generalError.value = error?.message || '提交失败，请稍后重试'
  } finally {
    // 任何 await 链上的异常都必须复位提交态，否则按钮永久禁用。
    submitting.value = false
  }
}
</script>

<template>
  <teleport to="body">
    <div v-if="modelValue" class="modal-scrim modal-scrim--sheet" @click.self="onClose">
      <div class="modal-card modal-sheet !max-w-[560px]" role="dialog" aria-modal="true" aria-label="提交结单">
        <div class="flex items-center justify-between gap-3">
          <h3 class="text-lg font-semibold text-ink-1">提交结单</h3>
          <button type="button" class="btn-ghost !min-h-[44px] !px-3" :disabled="submitting" @click="onClose">关闭</button>
        </div>

        <div v-if="generalError" class="message-error mt-3">{{ generalError }}</div>

        <div class="mt-4 space-y-4">
          <div>
            <label class="label" for="deliver-note">汇报说明（可选，最多 2000 字）</label>
            <textarea
              id="deliver-note"
              v-model="note"
              class="input min-h-[96px] resize-y"
              rows="4"
              maxlength="2000"
              placeholder="例如：已完成目标段位，附截图…"
            ></textarea>
            <p class="helper-text flex justify-between gap-2">
              <span>将随结束汇报展示给老板</span>
              <span :class="noteLen > 2000 ? 'text-danger' : 'text-ink-3'">{{ noteLen }}/2000</span>
            </p>
          </div>

          <div>
            <label class="label">汇报图片（{{ requireDeliveryImage ? '本单必传：至少 1 张，' : '可选，' }}常见图片格式，最多 5 张，单张 ≤10MB）</label>
            <input
              type="file"
              accept="image/*,.heic,.heif,.avif,.bmp,.gif"
              multiple
              class="block w-full text-sm text-ink-2 file:mr-3 file:rounded-full file:border-0 file:bg-surface-3 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-ink-1 hover:file:bg-[var(--surface-3-hover)]"
              :disabled="submitting || remainingSlots <= 0"
              @change="onPick"
            />
            <p class="helper-text">当前已有 {{ effectiveCount }} 张，最多还能上传 {{ remainingSlots }} 张；超过 2MB 会自动压缩，上传失败可重试或移除。</p>
            <p v-if="serverAttachmentCount > 0 && existingAttachments.length === 0" class="helper-text mt-1">
              已保存 {{ serverAttachmentCount }} 张草稿附件；当前页面未传入附件明细，弹窗无法显示具体图片，但可通过删除按钮事件接入服务器删除。
            </p>
            <p v-if="needImage" class="message-error mt-2">该订单要求上传完成截图后才能提交结单：请先选择图片，再点击提交（会上传图片并提交结单申请）。</p>

            <div v-if="Array.isArray(existingAttachments) && existingAttachments.length" class="mt-3">
              <p class="helper-text mb-2">已保存的草稿附件（{{ existingAttachments.length }} 张）</p>
              <div class="grid grid-cols-3 gap-3 sm:grid-cols-5">
                <div v-for="(attachment, index) in existingAttachments" :key="attachment.id || attachment.url || index" class="relative overflow-hidden rounded-tile border border-line-1 bg-surface-2">
                  <img v-if="attachment?.url" :src="attachment.url" :alt="attachment.filename || attachment.name || `已保存附件 ${index + 1}`" class="h-20 w-full object-cover" />
                  <div v-else class="flex h-20 items-center justify-center px-2 text-center text-xs text-ink-3">{{ attachment.filename || attachment.name || `已保存附件 ${index + 1}` }}</div>
                  <button
                    v-if="Array.isArray(existingAttachments)"
                    type="button"
                    class="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-black/65 text-xs text-white"
                    :disabled="submitting"
                    :aria-label="`删除已保存附件 ${index + 1}`"
                    :title="`删除已保存附件 ${index + 1}`"
                    @click="deleteExistingAt(index)"
                  >×</button>
                </div>
              </div>
            </div>

            <div v-if="files.length" class="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-5">
              <div v-for="(url, index) in previews" :key="files[index]?.name || index" class="relative overflow-hidden rounded-tile border border-line-1 bg-surface-2">
                <img :src="url" :alt="files[index]?.name || ''" class="h-20 w-full object-cover" />
                <div class="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-white/80 px-1 py-1 text-[10px] backdrop-blur dark:bg-black/40">
                  <span class="truncate text-ink-2">{{ uploadStates[index] === 'uploading' ? '上传中…' : uploadStates[index] === 'error' ? '失败' : '待上传' }}</span>
                  <button
                    v-if="uploadStates[index] === 'error'"
                    type="button"
                    class="rounded-full bg-danger px-2 py-1 font-semibold text-on-primary"
                    :disabled="submitting"
                    @click="retryOne(index)"
                  >重试</button>
                </div>
                <button
                  type="button"
                  class="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-xs text-white"
                  :disabled="submitting"
                  title="移除图片"
                  aria-label="移除"
                  @click="removeAt(index)"
                >×</button>
                <p v-if="uploadErrors[index]" class="px-1 py-1 text-[10px] leading-3 text-danger">{{ uploadErrors[index] }}</p>
              </div>
            </div>
          </div>
        </div>

        <div class="mt-6 flex gap-3">
          <button type="button" class="btn-secondary flex-1" :disabled="submitting" @click="onClose">取消</button>
          <button type="button" class="btn-success flex-1" :disabled="!canSubmit" @click="handleSubmit">{{ submitting ? '提交中…' : needImage ? '先上传截图' : '提交结单' }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>
