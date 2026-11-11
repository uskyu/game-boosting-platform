<script setup>
import { computed, onMounted, ref } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { BLACKLIST_BLOCK_NOTICE, useBlacklistStore } from '@/stores/blacklist'
import { formatDateTime } from '@/utils/display'

const PAGE_SIZE = 20

const authStore = useAuthStore()
const blacklistStore = useBlacklistStore()

// 黑名单是发单人（或管理员）的能力：普通打手即使直达页面也不请求接口，
// 后端同样会 403 兜底。
const canPublish = computed(() => authStore.canPublish)

const page = ref(1)
const listLoading = ref(false)
const listError = ref('')
const lookupQuery = ref('')
const lookupMessage = ref({ type: '', text: '' })
const addReason = ref('')
const addError = ref('')
const removeError = ref('')

const items = computed(() => blacklistStore.items)
const pagination = computed(() => blacklistStore.pagination)
const lookupUser = computed(() => blacklistStore.lookupResult)
const lookupAlreadyBlocked = computed(
  () => Boolean(lookupUser.value) && blacklistStore.isBlocked(lookupUser.value.id),
)

function avatarText(username) {
  return (username || 'U').slice(0, 1).toUpperCase()
}

function messageClass(type) {
  if (type === 'success') return 'message-success'
  if (type === 'error') return 'message-error'
  return 'message-info'
}

async function fetchPage(target = 1) {
  page.value = target
  listLoading.value = true
  listError.value = ''
  removeError.value = ''
  const result = await blacklistStore.fetchMine(target, PAGE_SIZE)
  listLoading.value = false
  if (!result.success) {
    listError.value = result.error || '黑名单加载失败，请稍后重试'
    return
  }
  page.value = pagination.value.page || target
}

// 只按用户名/登录邮箱完全匹配查找一个人：后端不提供候选列表/模糊搜索，
// 查不到就显示固定文案。
async function handleLookup() {
  lookupMessage.value = { type: '', text: '' }
  addError.value = ''
  const result = await blacklistStore.lookup(lookupQuery.value)
  if (!result.success) {
    lookupMessage.value = {
      type: result.notFound || result.invalid ? 'info' : 'error',
      text: blacklistStore.lookupError,
    }
  }
}

function cancelLookup() {
  blacklistStore.clearLookup()
  lookupMessage.value = { type: '', text: '' }
  addReason.value = ''
  addError.value = ''
}

async function confirmAddLookup() {
  if (!lookupUser.value || lookupAlreadyBlocked.value || blacklistStore.submitting) return
  addError.value = ''
  const target = lookupUser.value
  const result = await blacklistStore.add(target.id, addReason.value)
  if (!result.success) {
    addError.value = result.error || '拉黑失败，请稍后重试'
    return
  }
  const username = target.username || `用户 #${target.id}`
  blacklistStore.clearLookup()
  lookupQuery.value = ''
  addReason.value = ''
  lookupMessage.value = { type: 'success', text: `已拉黑 ${username}` }
  await fetchPage(1)
}

async function unblock(item) {
  if (!item?.blocked_user_id || blacklistStore.submitting) return
  const username = item.username || `用户 #${item.blocked_user_id}`
  if (!window.confirm(`确定解除对 ${username} 的拉黑吗？解除后对方立即恢复接你的单。`)) {
    return
  }
  removeError.value = ''
  const result = await blacklistStore.remove(item.blocked_user_id)
  if (!result.success) {
    removeError.value = result.error || '解除失败，请稍后重试'
    return
  }
  // 当前页被解除空了而后面还有页：退一页，避免停在空白页
  if (items.value.length === 0 && page.value > 1) {
    await fetchPage(page.value - 1)
  }
}

function changePage(target) {
  if (target < 1 || target > pagination.value.pages || target === page.value) return
  fetchPage(target)
}

onMounted(() => {
  if (canPublish.value) fetchPage(1)
})
</script>

<template>
  <div class="page-shell space-y-6">
    <section class="surface-card p-6 sm:p-8">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h1 class="section-title">黑名单</h1>
          <p class="mt-2 max-w-3xl text-sm leading-6 text-ink-2">
            被拉黑用户仍然可以看到你的订单，只是不能接单；已接订单、聊天和售后不受影响。
            <span class="block text-xs text-ink-3">{{ BLACKLIST_BLOCK_NOTICE }}</span>
          </p>
        </div>
        <router-link :to="{ name: 'orders', query: { tab: 'published' } }" class="btn-secondary shrink-0 !px-4 !py-2">
          返回我的派单
        </router-link>
      </div>
    </section>

    <p v-if="!canPublish" class="message-warning">黑名单仅发单用户可用。如你需要发单权限，请联系管理员开通。</p>

    <template v-else>
      <section class="surface-card p-6 sm:p-8">
        <h2 class="text-lg font-semibold text-ink-1">添加拉黑</h2>
        <p class="mt-1 text-xs leading-6 text-ink-3">
          只能按用户名或登录邮箱<strong class="text-ink-2">完全一致</strong>查找一个人；不支持模糊搜索，也不会展示其他用户。
        </p>
        <div class="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]">
          <input
            v-model="lookupQuery"
            type="text"
            class="input"
            maxlength="254"
            placeholder="用户名 / 登录邮箱（需完全一致）"
            @keyup.enter="handleLookup"
          />
          <button
            type="button"
            class="btn-primary !px-5 !py-2.5"
            :disabled="blacklistStore.lookupLoading || !lookupQuery.trim()"
            @click="handleLookup"
          >
            {{ blacklistStore.lookupLoading ? '查找中…' : '查找' }}
          </button>
        </div>

        <p v-if="lookupMessage.text" :class="[messageClass(lookupMessage.type), 'mt-3 text-sm leading-6']">
          {{ lookupMessage.text }}
        </p>

        <!-- 命中：只展示这一个用户卡片，确认后可选填备注；不渲染任何候选列表 -->
        <div v-if="lookupUser" class="info-tile mt-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex min-w-0 items-center gap-3">
              <img
                v-if="lookupUser.avatar_url"
                :src="lookupUser.avatar_url"
                :alt="lookupUser.username"
                class="h-10 w-10 rounded-full border border-line-1 object-cover"
                loading="lazy"
              />
              <div
                v-else
                class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line-1 bg-primary-soft text-sm font-semibold text-primary"
              >
                {{ avatarText(lookupUser.username) }}
              </div>
              <div class="min-w-0">
                <p class="truncate text-sm font-semibold text-ink-1">{{ lookupUser.username }}</p>
                <p class="mt-0.5 text-xs text-ink-3">用户 #{{ lookupUser.id }}</p>
              </div>
            </div>
            <span v-if="lookupAlreadyBlocked" class="tag !bg-surface-3 !text-ink-2">已拉黑</span>
          </div>

          <div v-if="!lookupAlreadyBlocked" class="mt-3">
            <label class="label" for="blacklist-reason">备注（选填，仅你自己可见）</label>
            <input
              id="blacklist-reason"
              v-model="addReason"
              type="text"
              class="input"
              maxlength="200"
              placeholder="例如：多次沟通无果"
            />
            <p v-if="addError" class="message-error mt-2 text-sm">{{ addError }}</p>
            <div class="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                class="btn-danger !px-4 !py-2"
                :disabled="blacklistStore.submitting"
                @click="confirmAddLookup"
              >
                {{ blacklistStore.submitting ? '提交中…' : '确认拉黑' }}
              </button>
              <button type="button" class="btn-ghost !px-4 !py-2" :disabled="blacklistStore.submitting" @click="cancelLookup">
                取消
              </button>
            </div>
          </div>
          <div v-else class="mt-3">
            <p class="text-xs leading-6 text-ink-3">{{ BLACKLIST_BLOCK_NOTICE }} 可在下方列表解除。</p>
            <button type="button" class="btn-ghost mt-1 !px-3 !py-1 text-xs" @click="cancelLookup">取消</button>
          </div>
        </div>
      </section>

      <section class="surface-card p-6 sm:p-8">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="text-lg font-semibold text-ink-1">
            已拉黑用户
            <span v-if="pagination.total" class="ml-1 text-sm tabular-nums text-ink-2">({{ pagination.total }})</span>
          </h2>
          <button
            type="button"
            class="btn-ghost !min-h-[36px] !px-4 !py-1.5 text-xs"
            :disabled="listLoading"
            @click="fetchPage(page)"
          >
            {{ listLoading ? '刷新中…' : '刷新' }}
          </button>
        </div>

        <div v-if="listLoading && !items.length" class="mt-4 space-y-3" aria-busy="true">
          <div v-for="n in 3" :key="`blacklist-skeleton-${n}`" class="skeleton-row">
            <div class="flex-1 space-y-3">
              <div class="skeleton-line h-4 w-2/5"></div>
              <div class="skeleton-line h-3 w-3/5"></div>
            </div>
          </div>
        </div>

        <div v-else-if="items.length" class="mt-4 space-y-3">
          <div v-for="item in items" :key="item.id || item.blocked_user_id" class="info-tile">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="flex min-w-0 items-center gap-3">
                <img
                  v-if="item.avatar_url"
                  :src="item.avatar_url"
                  :alt="item.username"
                  class="h-10 w-10 rounded-full border border-line-1 object-cover"
                  loading="lazy"
                />
                <div
                  v-else
                  class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line-1 bg-primary-soft text-sm font-semibold text-primary"
                >
                  {{ avatarText(item.username) }}
                </div>
                <div class="min-w-0">
                  <p class="truncate text-sm font-semibold text-ink-1">{{ item.username || `用户 #${item.blocked_user_id}` }}</p>
                  <p class="mt-0.5 text-xs text-ink-3">
                    用户 #{{ item.blocked_user_id }} · 拉黑于 {{ formatDateTime(item.created_at) }}
                  </p>
                  <p v-if="item.reason" class="mt-1 break-words text-xs leading-5 text-ink-2">备注：{{ item.reason }}</p>
                </div>
              </div>
              <button
                type="button"
                class="btn-secondary shrink-0 !px-4 !py-2"
                :disabled="blacklistStore.submitting"
                @click="unblock(item)"
              >
                解除
              </button>
            </div>
          </div>
        </div>

        <section v-else-if="!listError" class="empty-state py-10">
          <div class="empty-state__icon" aria-hidden="true">🚫</div>
          <h3 class="empty-state__title">黑名单为空</h3>
          <p class="empty-state__copy">你还没有拉黑任何人。拉黑后对方仍能看到你的订单，但不能接单。</p>
        </section>

        <p v-if="listError" class="message-error mt-3 text-sm">
          {{ listError }}
          <button type="button" class="btn-ghost !min-h-[32px] !px-3 !py-1 text-xs" @click="fetchPage(page)">重新加载</button>
        </p>
        <p v-if="removeError" class="message-error mt-3 text-sm">{{ removeError }}</p>

        <div v-if="pagination.pages > 1" class="mt-5 flex flex-col gap-3 border-t border-line-1 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-sm text-ink-2">{{ page }} / {{ pagination.pages }} · 共 {{ pagination.total }} 人</p>
          <div class="flex items-center gap-2">
            <button class="btn-secondary !px-4 !py-2" :disabled="page <= 1 || listLoading" @click="changePage(page - 1)">上一页</button>
            <button class="btn-secondary !px-4 !py-2" :disabled="page >= pagination.pages || listLoading" @click="changePage(page + 1)">下一页</button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
