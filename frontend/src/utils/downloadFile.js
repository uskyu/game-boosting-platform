// 浏览器下载二进制文件（导出 .xls 等）：objectURL + 游离 <a download>。
// 与 store 解耦，方便在无 DOM 的单测环境里安全 import。

const DEFAULT_DOWNLOAD_FILENAME = 'download.xls'

// 头里可能带路径 / 控制字符，只保留纯文件名；非法值一律当作没有
function sanitizeFilename(value) {
  const text = String(value ?? '')
    .replace(/[\r\n\t]/g, '')
    .trim()
  if (!text) {
    return null
  }
  const cleaned = text.split(/[\\/]/).pop()?.trim() || ''
  if (!cleaned || cleaned === '.' || cleaned === '..') {
    return null
  }
  return cleaned
}

// 解析 Content-Disposition 里的文件名：
//   attachment; filename="支付宝批量付款_9.xls"
//   attachment; filename*=utf-8''%E6%94%AF%E4%BB%98%E5%AE%9D.xls
// 取不到就返回 null，由调用方决定兜底名。
export function parseContentDispositionFilename(headerValue) {
  if (typeof headerValue !== 'string') {
    return null
  }
  const header = headerValue.trim()
  if (!header) {
    return null
  }

  // RFC 5987 扩展写法优先（后端中文文件名走这条）
  const extended = header.match(/filename\*\s*=\s*([^';]*)'[^']*'([^;]+)/i)
  if (extended) {
    const charset = (extended[1] || '').trim().toLowerCase()
    const raw = extended[2].trim()
    if (raw) {
      if (charset && charset !== 'utf-8') {
        // 非 utf-8 编码不猜，保留后端给的原文
        return sanitizeFilename(raw)
      }
      try {
        return sanitizeFilename(decodeURIComponent(raw))
      } catch {
        // 百分比编码不合法（后端转码出错）：宁可没有名字让调用方走兜底
        return null
      }
    }
  }

  const quoted = header.match(/filename\s*=\s*"([^"]*)"/i)
  if (quoted) {
    return sanitizeFilename(quoted[1])
  }

  const plain = header.match(/filename\s*=\s*([^;]+)/i)
  if (plain) {
    return sanitizeFilename(plain[1])
  }

  return null
}

export function downloadBlobFile(blob, filename) {
  const name =
    typeof filename === 'string' && filename.trim() ? filename.trim() : DEFAULT_DOWNLOAD_FILENAME

  // 无 DOM（SSR / node 单测）时静默跳过，不让调用方炸在下载这一步
  if (
    typeof document === 'undefined' ||
    typeof URL === 'undefined' ||
    typeof URL.createObjectURL !== 'function'
  ) {
    return false
  }

  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = name
  anchor.click()
  // Safari 必须在当前事件循环结束后才 revoke，否则下载会被取消
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  return true
}
