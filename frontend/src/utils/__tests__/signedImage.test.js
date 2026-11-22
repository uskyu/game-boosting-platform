import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// api 会拉入 auth store / router（真实实现依赖 Pinia 与浏览器路由），这里
// 按现有测试的约定把它们换成可控替身，只验证缓存与重签逻辑本身。
const http = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('@/utils/api', () => ({ default: http }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ accessToken: 'test-token' }) }))
vi.mock('@/router', () => ({ default: { push: vi.fn(), currentRoute: { value: { fullPath: '/' } } } }))

import {
  MIN_REMAINING_MS,
  STORAGE_KEY,
  cacheSignedUrl,
  clearSignedUrlCache,
  expiresAtFromSignedUrl,
  handleImageError,
  hasSignedUrlCacheEntry,
  isSignedCosUrl,
  rawKeyFor,
  resolveImageUrl,
} from '../signedImage'

// 极简 localStorage 替身（vitest 默认 environment: node，没有 localStorage）
function installLocalStorage() {
  const store = new Map()
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    clear: () => store.clear(),
  }
  return store
}

function readStore() {
  const raw = globalThis.localStorage.getItem(STORAGE_KEY)
  return raw ? JSON.parse(raw) : {}
}

const RAW = '/uploads/orders/pic.jpg'
const SIGNED = 'https://game-1314124236.cos.ap-chongqing.myqcloud.com/orders/pic.jpg?q-sign-algorithm=sha1&q-ak=AKIDfake&q-sign-time=1000;2000&q-signature=sig'
const TTL_7D_MS = 7 * 24 * 3600 * 1000

beforeEach(() => {
  installLocalStorage()
  http.post.mockReset()
})

afterEach(() => {
  clearSignedUrlCache()
  vi.restoreAllMocks()
})

describe('resolveImageUrl 缓存解析', () => {
  it('无缓存时原样返回后端下发的地址', () => {
    expect(resolveImageUrl(RAW)).toBe(RAW)
    expect(resolveImageUrl(SIGNED)).toBe(SIGNED)
  })

  it('缓存剩余有效期超过 1 小时时命中缓存的签名 URL', () => {
    const now = 1_700_000_000_000
    cacheSignedUrl(RAW, SIGNED, now + TTL_7D_MS)
    expect(resolveImageUrl(RAW, now)).toBe(SIGNED)
    // 用签名 URL 作为入参也应命中同一条记录（缓存键被规范化成 /uploads 路径）
    expect(resolveImageUrl(SIGNED, now)).toBe(SIGNED)
  })

  it('剩余有效期不足 1 小时（阈值边界）时不信任缓存', () => {
    const now = 1_700_000_000_000
    // 恰好等于阈值：要求严格大于，所以仍视为不可用
    cacheSignedUrl(RAW, SIGNED, now + MIN_REMAINING_MS)
    expect(resolveImageUrl(RAW, now)).toBe(RAW)
    // 多 1 毫秒即可命中
    cacheSignedUrl(RAW, SIGNED, now + MIN_REMAINING_MS + 1)
    expect(resolveImageUrl(RAW, now)).toBe(SIGNED)
  })

  it('已过期的缓存条目被忽略', () => {
    const now = 1_700_000_000_000
    cacheSignedUrl(RAW, SIGNED, now - 1000)
    expect(resolveImageUrl(RAW, now)).toBe(RAW)
  })

  it('损坏的 localStorage 数据不会抛错', () => {
    globalThis.localStorage.setItem(STORAGE_KEY, '{not json')
    expect(resolveImageUrl(RAW)).toBe(RAW)
    globalThis.localStorage.setItem(STORAGE_KEY, '[1,2,3]')
    expect(resolveImageUrl(RAW)).toBe(RAW)
  })

  it('空地址返回空串', () => {
    expect(resolveImageUrl('')).toBe('')
    expect(resolveImageUrl(null)).toBe('')
  })
})

describe('rawKeyFor / isSignedCosUrl 规范化', () => {
  it('识别本站 /uploads 路径与 COS 签名 URL', () => {
    expect(rawKeyFor(RAW)).toBe(RAW)
    expect(rawKeyFor(SIGNED)).toBe(RAW)
    expect(isSignedCosUrl(SIGNED)).toBe(true)
    expect(isSignedCosUrl(RAW)).toBe(false)
  })

  it('外链与非法路径不参与缓存/重签', () => {
    expect(rawKeyFor('https://cdn.example/avatar.png')).toBeNull()
    expect(rawKeyFor('/uploads/../secret')).toBeNull()
    expect(rawKeyFor('/uploads/orders/x.jpg?token=1')).toBeNull()
    expect(rawKeyFor('')).toBeNull()
    expect(isSignedCosUrl('https://cdn.example/avatar.png')).toBe(false)
  })
})

describe('hasSignedUrlCacheEntry', () => {
  it('缓存里有该 key 的签名记录时返回 true（用于判断是否值得重签）', () => {
    cacheSignedUrl(RAW, SIGNED, 2000 * 1000)
    expect(hasSignedUrlCacheEntry(RAW)).toBe(true)
    expect(hasSignedUrlCacheEntry(SIGNED)).toBe(true)
  })

  it('无记录 / 外链地址返回 false', () => {
    expect(hasSignedUrlCacheEntry(RAW)).toBe(false)
    expect(hasSignedUrlCacheEntry('https://cdn.example/avatar.png')).toBe(false)
    expect(hasSignedUrlCacheEntry('')).toBe(false)
  })
})

describe('expiresAtFromSignedUrl', () => {
  it('从 q-sign-time 解析到期时刻（秒 → 毫秒）', () => {
    expect(expiresAtFromSignedUrl(SIGNED)).toBe(2000 * 1000)
  })

  it('缺少/损坏的 q-sign-time 返回 null', () => {
    expect(expiresAtFromSignedUrl('/uploads/orders/pic.jpg')).toBeNull()
    expect(expiresAtFromSignedUrl('https://cos.example/x.jpg?q-signature=1')).toBeNull()
    expect(expiresAtFromSignedUrl('https://cos.example/x.jpg?q-sign-time=abc')).toBeNull()
  })
})

describe('handleImageError 过期重签', () => {
  it('调用后端一次并在成功后写入缓存', async () => {
    const fresh = 'https://game-1314124236.cos.ap-chongqing.myqcloud.com/orders/pic.jpg?q-sign-time=3000;4000&q-signature=new'
    http.post.mockResolvedValueOnce({ data: { url: fresh } })

    const renewed = await handleImageError(SIGNED)

    expect(renewed).toBe(fresh)
    expect(http.post).toHaveBeenCalledTimes(1)
    expect(http.post).toHaveBeenCalledWith('/storage/resign', { url: RAW })
    // 缓存键规范化成 /uploads 路径，到期时刻来自新 URL
    expect(readStore()[RAW]).toEqual({ url: fresh, expiresAt: 4000 * 1000 })
    // 用「签名期内、且剩余超过 1 小时」的时间点断言命中
    // （fixture 的 q-sign-time 是刻意选小的时间戳，便于手算边界）
    expect(resolveImageUrl(RAW, 300 * 1000)).toBe(fresh)
  })

  it('并发调用同一张图只发一次请求', async () => {
    const fresh = 'https://cos.example/orders/pic.jpg?q-sign-time=3000;4000&q-signature=new'
    http.post.mockResolvedValue({ data: { url: fresh } })

    const [a, b] = await Promise.all([
      handleImageError(SIGNED),
      handleImageError(SIGNED),
    ])
    expect(a).toBe(fresh)
    expect(b).toBe(fresh)
    expect(http.post).toHaveBeenCalledTimes(1)
  })

  it('后端拒绝（403）时返回 null，且不写入缓存', async () => {
    http.post.mockRejectedValueOnce({ status: 403, message: '无权访问该图片' })
    expect(await handleImageError(SIGNED)).toBeNull()
    expect(readStore()).toEqual({})
    expect(resolveImageUrl(RAW)).toBe(RAW)
  })

  it('不进入重试循环：一次失败后调用方再调用才会再发请求，单次调用只发一次', async () => {
    http.post.mockRejectedValue({ status: 403 })
    expect(await handleImageError(SIGNED)).toBeNull()
    expect(http.post).toHaveBeenCalledTimes(1)
    // 单个 handleImageError 内部绝不重试
    expect(await handleImageError(SIGNED)).toBeNull()
    expect(http.post).toHaveBeenCalledTimes(2)
  })

  it('非本站地址（外链头像）不发重签请求', async () => {
    expect(await handleImageError('https://cdn.example/avatar.png')).toBeNull()
    expect(http.post).not.toHaveBeenCalled()
  })

  it('后端缺 url 字段时按失败处理', async () => {
    http.post.mockResolvedValueOnce({ data: {} })
    expect(await handleImageError(SIGNED)).toBeNull()
    expect(readStore()).toEqual({})
  })

  it('新 URL 缺 q-sign-time 时用保守 TTL 写入缓存', async () => {
    const before = Date.now()
    http.post.mockResolvedValueOnce({ data: { url: 'https://cos.example/x.jpg' } })
    const renewed = await handleImageError(SIGNED)
    expect(renewed).toBe('https://cos.example/x.jpg')
    const entry = readStore()[RAW]
    expect(entry.url).toBe('https://cos.example/x.jpg')
    expect(entry.expiresAt).toBeGreaterThanOrEqual(before + 6 * 24 * 3600 * 1000)
  })
})
