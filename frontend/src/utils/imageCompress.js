// 交付截图客户端压缩：手机原图常见 3-10MB，弱网下直传动辄超过浏览器
// 60s 网络超时被掐断（服务端日志 499、前端报「网络连接失败」，老板 2026-10-15 反馈）。
// 超过 2MB 的图片在选图阶段先压到长边 1920 的 JPEG 再上传，体积通常降到十分之一内。

const THRESHOLD_BYTES = 2 * 1024 * 1024
const MAX_DIMENSION = 1920
const QUALITIES = [0.85, 0.72, 0.62]

export function shouldCompressImage(file) {
  return Boolean(file)
    && typeof file.type === 'string'
    && file.type.startsWith('image/')
    && file.type !== 'image/gif'
    && file.size > THRESHOLD_BYTES
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const image = new Image()
    image.onload = () => { URL.revokeObjectURL(url); resolve(image) }
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('image decode failed')) }
    image.src = url
  })
}

function canvasToBlob(canvas, quality) {
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob || null), 'image/jpeg', quality)
    } catch {
      resolve(null)
    }
  })
}

// 压缩失败一律回退原文件：宁可慢，不可丢图。
export async function compressImageFile(file) {
  if (!shouldCompressImage(file)) return file
  try {
    const image = await loadImage(file)
    const width = image.naturalWidth || image.width
    const height = image.naturalHeight || image.height
    if (!width || !height) return file
    const scale = Math.min(1, MAX_DIMENSION / Math.max(width, height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(width * scale))
    canvas.height = Math.max(1, Math.round(height * scale))
    const context = canvas.getContext('2d')
    if (!context) return file
    context.drawImage(image, 0, 0, canvas.width, canvas.height)

    let best = null
    for (const quality of QUALITIES) {
      const blob = await canvasToBlob(canvas, quality)
      if (!blob) break
      if (!best || blob.size < best.size) best = blob
      if (blob.size <= THRESHOLD_BYTES) break
    }
    if (!best || best.size >= file.size) return file
    const name = `${file.name.replace(/\.[^.]+$/, '')}.jpg`
    return new File([best], name, { type: 'image/jpeg', lastModified: Date.now() })
  } catch {
    return file
  }
}
