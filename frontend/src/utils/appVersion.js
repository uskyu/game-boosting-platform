/**
 * 前端版本自愈工具。
 *
 * 背景（2026-11-19）：微信 XWEB / 老浏览器可能长期保留旧 index.html 与旧
 * 懒加载 chunk。旧前端不认识「指定派单」，点「续单」会把订单按公开单发布，
 * 结果全站打手都能看到。这里提供两个纯函数：
 * - extractModuleEntry：从 index.html 提取入口脚本，用于比较版本；
 * - isChunkLoadError：识别懒加载 chunk 404 导致的加载失败，触发一次重载。
 */

const MODULE_SCRIPT_PATTERNS = [
  /<script[^>]*type="module"[^>]*src="([^"]+)"/i,
  /<script[^>]*src="([^"]+)"[^>]*type="module"/i,
]

const BUILD_STAMP_PATTERNS = [
  /<meta[^>]*name="app-build"[^>]*content="([^"]+)"/i,
  /<meta[^>]*content="([^"]+)"[^>]*name="app-build"/i,
]

const CHUNK_ERROR_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /error loading dynamically imported module/i,
  /ChunkLoadError/i,
]

export function extractModuleEntry(html) {
  if (!html) return ''
  for (const pattern of MODULE_SCRIPT_PATTERNS) {
    const match = html.match(pattern)
    if (match?.[1]) return match[1]
  }
  return ''
}

export function extractBuildStamp(html) {
  if (!html) return ''
  for (const pattern of BUILD_STAMP_PATTERNS) {
    const match = html.match(pattern)
    if (match?.[1]) return match[1]
  }
  return ''
}

export function isChunkLoadError(error) {
  const message = String(error?.message || error || '')
  if (!message) return false
  return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message))
}
