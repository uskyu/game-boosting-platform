<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import api from '@/utils/api'

const router = useRouter()
const authStore = useAuthStore()

const templates = ref([])
const messages = ref([])
const inputText = ref('')
const sending = ref(false)
const transferring = ref(false)
const chatContainer = ref(null)
const showCategoryTemplates = ref(null)

const greeting = computed(() => {
  const name = authStore.user?.username || '用户'
  return `你好 ${name}！我是AI智能客服，请问有什么可以帮助您的？\n\n您可以选择下方的常见问题快速咨询，也可以直接输入您的问题。`
})

function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

function addBotMessage(data) {
  messages.value.push({
    id: Date.now(),
    role: 'bot',
    text: data.reply,
    category: data.category,
    actions: data.actions || [],
    needHuman: data.need_human || false,
    time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
  })
  scrollToBottom()
}

function addUserMessage(text) {
  messages.value.push({
    id: Date.now(),
    role: 'user',
    text,
    time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
  })
  scrollToBottom()
}

async function sendTemplateMessage(template) {
  addUserMessage(template.text)
  sending.value = true

  try {
    const res = await api.post('/support/chat', {
      message: template.text,
      template_key: template.key,
    })
    addBotMessage(res.data)
  } catch {
    addBotMessage({
      reply: '抱歉，请求失败，请稍后重试。',
      category: 'general',
      actions: [],
      need_human: true,
    })
  } finally {
    sending.value = false
  }
}

async function sendFreeMessage() {
  const text = inputText.value.trim()
  if (!text || sending.value) return

  inputText.value = ''
  addUserMessage(text)
  sending.value = true

  try {
    const res = await api.post('/support/chat', { message: text })
    addBotMessage(res.data)
  } catch {
    addBotMessage({
      reply: '抱歉，AI客服暂时无法回答。建议您转接人工客服。',
      category: 'general',
      actions: [{ label: '转人工客服', type: 'transfer', link: '' }],
      need_human: true,
    })
  } finally {
    sending.value = false
  }
}

async function handleAction(action) {
  if (action.type === 'navigate' && action.link) {
    router.push(action.link)
  } else if (action.type === 'transfer') {
    await transferToHuman()
  }
}

async function transferToHuman() {
  if (transferring.value) return
  transferring.value = true

  try {
    const res = await api.post('/support/transfer-human')
    addBotMessage({
      reply: `已为您转接人工客服 ${res.data.admin_username}，正在跳转聊天页面...`,
      category: 'general',
      actions: [],
      need_human: false,
    })

    setTimeout(() => {
      router.push({ name: 'chat-detail', params: { id: res.data.conversation_id } })
    }, 1500)
  } catch (err) {
    addBotMessage({
      reply: err.message || '转接失败，请稍后重试。',
      category: 'general',
      actions: [],
      need_human: true,
    })
  } finally {
    transferring.value = false
  }
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendFreeMessage()
  }
}

onMounted(async () => {
  try {
    const res = await api.get('/support/templates')
    templates.value = res.data
  } catch {
    // ignore
  }
})
</script>

<template>
  <div class="page-shell">
    <div class="mx-auto flex h-[calc(100vh-120px)] max-w-4xl flex-col">
      <!-- Header -->
      <div class="flex items-center gap-3 border-b border-line-1 pb-4">
        <div class="flex h-10 w-10 items-center justify-center rounded-tile border border-line-1 bg-primary-soft text-lg">
          🤖
        </div>
        <div>
          <h1 class="text-lg font-semibold text-ink-1">AI 智能客服</h1>
          <p class="text-xs text-ink-2">7x24小时在线 · 快速响应</p>
        </div>
        <button class="btn-ghost ml-auto !px-4 text-sm" @click="transferToHuman" :disabled="transferring">
          {{ transferring ? '转接中...' : '转人工客服' }}
        </button>
      </div>

      <!-- Chat area -->
      <div ref="chatContainer" class="flex-1 space-y-4 overflow-y-auto py-4">
        <!-- Greeting -->
        <div class="flex gap-3">
          <div class="chat-avatar !h-9 !w-9 !rounded-tile text-sm">🤖</div>
          <div class="chat-bubble chat-bubble-other max-w-[80%] !px-4 !py-3.5">
            <p class="whitespace-pre-line text-sm text-ink-1">{{ greeting }}</p>
          </div>
        </div>

        <!-- Template quick replies -->
        <div v-if="messages.length === 0" class="space-y-3 pl-12">
          <div v-for="cat in templates" :key="cat.key" class="space-y-2">
            <p class="text-xs font-medium text-ink-2">{{ cat.icon }} {{ cat.label }}</p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="t in cat.templates"
                :key="t.key"
                class="filter-pill !py-1.5 text-xs"
                :disabled="sending"
                @click="sendTemplateMessage(t)"
              >
                {{ t.text }}
              </button>
            </div>
          </div>
        </div>

        <!-- Messages -->
        <template v-for="msg in messages" :key="msg.id">
          <!-- User message -->
          <div v-if="msg.role === 'user'" class="flex justify-end gap-3">
            <div class="chat-bubble chat-bubble-self max-w-[80%] !px-4 !py-3.5">
              <p class="text-sm text-on-primary">{{ msg.text }}</p>
              <p class="mt-1 text-right text-[10px] text-on-primary opacity-60">{{ msg.time }}</p>
            </div>
          </div>

          <!-- Bot message -->
          <div v-else class="flex gap-3">
            <div class="chat-avatar !h-9 !w-9 !rounded-tile text-sm">🤖</div>
            <div class="max-w-[80%] space-y-3">
              <div class="chat-bubble chat-bubble-other !px-4 !py-3.5">
                <p class="whitespace-pre-line text-sm text-ink-1">{{ msg.text }}</p>
                <p class="mt-1 text-[10px] text-ink-3">{{ msg.time }}</p>
              </div>

              <!-- Action buttons -->
              <div v-if="msg.actions?.length" class="flex flex-wrap gap-2">
                <button
                  v-for="(action, idx) in msg.actions"
                  :key="idx"
                  class="btn-secondary !rounded-full !px-3.5 !py-1.5 text-xs"
                  @click="handleAction(action)"
                >
                  {{ action.label }}
                </button>
              </div>

              <!-- Transfer suggestion -->
              <div v-if="msg.needHuman && !msg.actions?.some(a => a.type === 'transfer')" class="flex gap-2">
                <button class="btn-ghost !rounded-full !border-warning/40 !px-3.5 !py-1.5 text-xs !text-warning hover:!bg-warning/10" @click="transferToHuman">
                  建议转人工客服
                </button>
              </div>
            </div>
          </div>
        </template>

        <!-- Typing indicator -->
        <div v-if="sending" class="flex gap-3">
          <div class="chat-avatar !h-9 !w-9 !rounded-tile text-sm">🤖</div>
          <div class="chat-bubble chat-bubble-other !px-4 !py-3.5">
            <div class="flex gap-1">
              <span class="chat-typing-dot !h-1.5 !w-1.5"></span>
              <span class="chat-typing-dot !h-1.5 !w-1.5"></span>
              <span class="chat-typing-dot !h-1.5 !w-1.5"></span>
            </div>
          </div>
        </div>
      </div>

      <!-- Quick templates (compact, after first message) -->
      <div v-if="messages.length > 0" class="border-t border-line-1 py-2">
        <div class="flex gap-2 overflow-x-auto">
          <button
            v-for="cat in templates"
            :key="cat.key"
            class="filter-pill shrink-0 text-xs"
            @click="showCategoryTemplates = showCategoryTemplates === cat.key ? null : cat.key"
          >
            {{ cat.icon }} {{ cat.label }}
          </button>
        </div>
        <div v-if="showCategoryTemplates" class="mt-2 flex flex-wrap gap-2">
          <button
            v-for="t in templates.find(c => c.key === showCategoryTemplates)?.templates || []"
            :key="t.key"
            class="filter-pill text-xs"
            :disabled="sending"
            @click="sendTemplateMessage(t)"
          >
            {{ t.text }}
          </button>
        </div>
      </div>

      <!-- Input area -->
      <div class="border-t border-line-1 pt-3 pb-2">
        <div class="flex items-end gap-3">
          <textarea
            v-model="inputText"
            rows="1"
            class="input flex-1 resize-none"
            placeholder="输入您的问题..."
            :disabled="sending"
            @keydown="handleKeydown"
          ></textarea>
          <button
            class="btn-primary shrink-0 !px-5 !py-2.5"
            :disabled="!inputText.trim() || sending"
            @click="sendFreeMessage"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
