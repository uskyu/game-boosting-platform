/**
 * COS 私有读图片的「签名 URL 本地缓存 + 过期重签」工具。
 *
 * 后端在 COS 模式下会把响应里的 `/uploads/<key>` 改写成 7 天预签名 URL，因此
 * `<img>` 收到的通常是一串带 `q-signature` 的 COS 地址。签名到期后 COS 直接
 * 返回 403，图片会「忽然加载失败」——这时需要向后端换一串新的。
 *
 * 本模块做三件事：
 * 1. **规范化缓存键**：把 COS 签名 URL 还原成 `/uploads/<key>`（`chromeImgKey`）。
 *    这样同一张图无论这轮拿到的是哪个窗口的签名，都命中同一条缓存记录；
 *    页面刷新时不会因为 URL 字符串变化而重复请求签名。
 * 2. **缓存签名结果**：`rawUrl -> { url, expiresAt }`，剩余有效期 > 1 小时
 *    时直接复用（与后端 `SIGN_SAFETY_REMAINING_SECONDS` 同一口径）。
 * 3. **失败重签**：加载失败时调一次 `POST /storage/resign` 换新 URL，写入缓存
 *    并返回；同一 URL 的并发失败合并成一个请求，且只重试一次（调用方负责
 *    不再循环）。
 *
 * 存进 localStorage 的只有图片 URL 与到期时间戳，不含任何凭据：签名 URL 本身
 * 只带 AKID 与签名参数，SecretKey 永远留在服务端。
 */

import api from '@/utils/api'

export const STORAGE_KEY = 'signed_img_v1'
// 与后端 SIGN_SAFETY_REMAINING_SECONDS=3600 对齐：剩余不足 1 小时不再信任缓存。
export const MIN_REMAINING_MS = 60 * 60 * 1000
// 无法从 URL 解析到期时刻时的保守缓存期（6 天，仍落在 7 天窗口内）。
const FALLBACK_TTL_MS = 6 * 24 * 60 * 60 * 1000

// 同一 key 的并发重签合并成一个请求：一次页面里多张同源图片 403 不该发多次
// （后端该接口还有 120 次/分钟的限流，批量重试很容易打满）。
const inflightResign = new Map()

function readCache() {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    // 只接受普通对象；被手工改坏或其他版本残留的数据一律丢弃，绝不让脏数据抛错
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

function writeCache(cache) {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(cache))
  } catch {
    // 存储写满 / 浏览器禁用（隐私模式）时静默降级：图片照常显示，只是不缓存
  }
}

function isValidEntry(entry, now) {
  return Boolean(
    entry
    && typeof entry.url === 'string'
    && entry.url
    && Number.isFinite(Number(entry.expiresAt))
    && Number(entry.expiresAt) - now > MIN_REMAINING_MS,
  )
}

function safeParseUrl(url) {
  try {
    return new URL(url, 'https://placeholder.local')
  } catch {
    return null
  }
}

/**
 * 判断是否为我们自己的 COS 预签名 URL（带签名参数，或腾讯云域名）。
 *
 * @param {string} url
 * @returns {boolean}
 */
export function isSignedCosUrl(url) {
  if (typeof url !== 'string' || !url) return false
  if (url.startsWith('/uploads/')) return false
  const parsed = safeParseUrl(url)
  if (!parsed) return false
  if (parsed.searchParams.has('q-signature') || parsed.searchParams.has('q-sign-algorithm')) {
    return true
  }
  return /(^|\.)(cos|myqcloud)\./i.test(parsed.hostname)
    || /\.myqcloud\.com$/i.test(parsed.hostname)
}

/**
 * 把后端响应里的图片地址规范化成缓存键 / 重签入参。
 *
 * - `/uploads/orders/x.jpg` → 原样返回（LOCAL 模式或历史路径）
 * - COS 签名 URL → `/uploads/<pathname>`（用于命中同一张图的缓存与重签）
 * - 其他绝对 URL（外链头像等）→ null（既不缓存也不能重签）
 *
 * @param {string} url
 * @returns {string|null}
 */
export function rawKeyFor(url) {
  if (typeof url !== 'string' || !url) return null
  if (url.startsWith('/uploads/')) {
    // 与后端 key_from_url 同一口径：拒绝查询串/片段/穿越
    if (url === '/uploads' || url === '/uploads/') return null
    if (url.includes('?') || url.includes('#') || url.includes('\\')) return null
    if (url.includes('/../') || url.includes('/./') || url.startsWith('/uploads/../')) return null
    return url
  }
  if (!isSignedCosUrl(url)) return null
  const parsed = safeParseUrl(url)
  if (!parsed) return null
  const pathname = parsed.pathname.replace(/^\/+/, '')
  if (!pathname || pathname.includes('..')) return null
  return `/uploads/${pathname}`
}

/**
 * 解析应使用的图片地址。
 *
 * 命中有效缓存（剩余 > 1 小时）返回缓存的签名 URL；否则原样返回入参
 * （它本身可能已经是后端给的有效签名 URL，也可能仍是 /uploads 路径）。
 *
 * @param {string} rawUrl 后端响应里的图片地址
 * @param {number} [now] 便于测试注入的「当前时间」
 * @returns {string} 可直接用于 `<img src>` 的地址
 */
export function resolveImageUrl(rawUrl, now = Date.now()) {
  if (typeof rawUrl !== 'string' || !rawUrl) return ''
  const key = rawKeyFor(rawUrl)
  if (!key) return rawUrl
  const entry = readCache()[key]
  if (isValidEntry(entry, now)) return entry.url
  return rawUrl
}

/**
 * 写入签名缓存。
 *
 * @param {string} rawUrl 后端响应里的地址或 `/uploads` 路径（会被规范化成键）
 * @param {string} signedUrl 已签名的 URL
 * @param {number} expiresAt 签名到期时间（毫秒时间戳）
 */
export function cacheSignedUrl(rawUrl, signedUrl, expiresAt) {
  const key = rawKeyFor(rawUrl)
  if (!key) return
  if (typeof signedUrl !== 'string' || !signedUrl) return
  const expires = Number(expiresAt)
  if (!Number.isFinite(expires)) return
  const cache = readCache()
  cache[key] = { url: signedUrl, expiresAt: expires }
  writeCache(cache)
}

/** 清空签名缓存（登出 / 切换账号时调用，避免把上一个账号的签名带过去）。 */
export function clearSignedUrlCache() {
  try {
    globalThis.localStorage?.removeItem(STORAGE_KEY)
  } catch {
    // 存储不可用：忽略
  }
}

/**
 * 该地址是否曾经签发过（缓存里有对应记录）。
 *
 * 用于区分「COS 部署里因临时签名失败而回落到 /uploads 的图」与「LOCAL 模式
 * 下真的不存在的本地文件」：前者值得重签一次，后者重签是纯噪音。
 *
 * @param {string} rawUrl
 * @returns {boolean}
 */
export function hasSignedUrlCacheEntry(rawUrl) {
  const key = rawKeyFor(rawUrl)
  if (!key) return false
  const entry = readCache()[key]
  return Boolean(entry && typeof entry.url === 'string' && entry.url)
}

/**
 * 从 URL 的 `q-sign-time=<start>;<end>` 解析签名到期时刻。
 *
 * 这是 COS 预签名里最可靠的到期来源；后端签名接口只回 URL 本身，前端据此
 * 推导 `expiresAt`。解析失败返回 null（调用方用保守 TTL 兜底）。
 *
 * @param {string} url
 * @returns {number|null} 毫秒时间戳
 */
export function expiresAtFromSignedUrl(url) {
  if (typeof url !== 'string' || !url) return null
  const parsed = safeParseUrl(url)
  if (!parsed) return null
  const signTime = parsed.searchParams.get('q-sign-time')
  if (!signTime) return null
  const end = Number(signTime.split(';')[1])
  if (!Number.isFinite(end) || end <= 0) return null
  return end * 1000
}

/**
 * 图片加载失败（签名过期 → COS 403）时的兜底：向后端换一串新签名 URL。
 *
 * 只对可规范化成本站 `/uploads` key 的地址生效；每次调用最多发一次请求，
 * 调用方必须保证不再循环重试（`SignedImage` 内部用 `retried` 标记）。
 *
 * @param {string} rawUrl 当前 `<img>` 正在使用的地址
 * @returns {Promise<string|null>} 新的签名 URL；无需/无法重签时返回 null
 */
export async function handleImageError(rawUrl) {
  const key = rawKeyFor(rawUrl)
  if (!key) return null

  if (inflightResign.has(key)) return inflightResign.get(key)

  const task = (async () => {
    try {
      const response = await api.post('/storage/resign', { url: key })
      const signedUrl = response?.data?.url
      if (typeof signedUrl !== 'string' || !signedUrl) return null
      const expiresAt = expiresAtFromSignedUrl(signedUrl) || Date.now() + FALLBACK_TTL_MS
      cacheSignedUrl(key, signedUrl, expiresAt)
      return signedUrl
    } catch {
      // 403（无权限）/ 401（登录失效）/ 网络失败：都不重试，交给调用方降级显示
      return null
    } finally {
      inflightResign.delete(key)
    }
  })()

  inflightResign.set(key, task)
  return task
}
