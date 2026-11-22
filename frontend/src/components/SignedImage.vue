<script setup>
/**
 * 带签名 URL 解析与过期重试的 `<img>` 包装组件。
 *
 * 用法：`<SignedImage :raw-url="getAttachment(order)" alt="订单附件" class="..." />`
 *
 * 行为：
 * 1. `src` 由 `resolveImageUrl(rawUrl)` 决定 —— localStorage 里有仍有效的签名
 *    就直接用，否则用后端本次下发的地址。**页面刷新不会重复请求签名。**
 * 2. `loading="lazy"` 保持不变（大厅列表里几十张图不该同时下载）。
 * 3. `error` 时调用一次 `handleImageError(rawUrl)` 换新签名并替换 `src`；
 *    用 `retried` 标记保证**只重试一次**，第二次失败就停在原 URL，绝不循环。
 *
 * 不 emit 任何事件：使用方的 `class`/`alt`/`@click` 等属性全部透传到 `<img>`
 * （`inheritAttrs` + `v-bind="$attrs"`），因此把 `<img>` 换成这个组件时布局与
 * 交互完全不变。
 */
import { computed, ref, watch } from 'vue'

import {
  hasSignedUrlCacheEntry,
  handleImageError,
  isSignedCosUrl,
  resolveImageUrl,
} from '@/utils/signedImage'

const props = defineProps({
  rawUrl: {
    type: String,
    default: '',
  },
})

defineOptions({ inheritAttrs: false })

// 后端本次下发的地址：变化时（换页、重新拉取）重新解析缓存，并复位重试标记
const currentUrl = ref(resolveImageUrl(props.rawUrl))
const retried = ref(false)

watch(
  () => props.rawUrl,
  (value) => {
    currentUrl.value = resolveImageUrl(value)
    retried.value = false
  },
)

/**
 * 是否值得重签。
 *
 * - COS 签名 URL：签名过期（403），必须换新 URL。
 * - 裸 `/uploads` 路径但本地缓存里有过该 key 的签名：说明站点在 COS 模式，
 *   这次只是签名临时失败回落到了原路径，值得再试一次。
 * - 其余情况（LOCAL 模式下真的读不到的历史文件）：重签没有意义，不发请求。
 */
const canResign = computed(() => {
  const url = currentUrl.value
  if (!url) return false
  if (isSignedCosUrl(url)) return true
  return Boolean(hasSignedUrlCacheEntry(url))
})

async function onError() {
  // 已经重试过：本次失败就是最终结果，不再发请求（防循环）
  if (retried.value) return
  retried.value = true
  if (!canResign.value) return
  const renewed = await handleImageError(currentUrl.value)
  // handleImageError 成功时已同步写入缓存（含缺 q-sign-time 的保守 TTL）；
  // 这里只要把 src 换掉即可，失败（null）则保持原图不重试。
  if (renewed) currentUrl.value = renewed
}
</script>

<template>
  <img
    v-if="currentUrl"
    v-bind="$attrs"
    :src="currentUrl"
    loading="lazy"
    decoding="async"
    @error="onError"
  />
</template>
