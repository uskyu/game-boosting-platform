/**
 * 图片直传 COS：浏览器 -> 对象存储，图片不再经服务器中转。
 *
 * 背景（2026-11-24）：对象存储上线后只有下载走了 COS 直连，上传仍是
 * 「浏览器 -> 后端 multipart -> 压缩/校验 -> COS」的双跳，服务器仍承担
 * 用户上传的全部带宽。这里改成三步直传：
 *   1) POST /uploads/presign 拿 PUT 预签名 URL（key 由后端生成）；
 *   2) 浏览器 PUT 到 COS（带进度回调）；
 *   3) POST /uploads/commit 让后端 HEAD 复核后写入订单。
 *
 * 兜底：LOCAL 模式 / 预签名失败 / 浏览器不支持压缩时返回 null，调用方
 * 继续走原来的 multipart 端点，行为保持一致。
 */

import api from '@/utils/api'

// 与后端 file_service 保持一致：超过 2MB 先在浏览器本地压到最长边 2000px、
// JPEG 质量 82，再上传。这样既保留线上「大图自动压缩」的成果，又让直传的
// 字节数最小。
const COMPRESS_THRESHOLD_BYTES = 2 * 1024 * 1024
const MAX_EDGE = 2000
const JPEG_QUALITY = 0.82
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
const ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/webp']

function isUploadableImage(file) {
  return Boolean(file) && ALLOWED_TYPES.includes((file.type || '').toLowerCase())
}

/**
 * 大图本地重编码。失败（老浏览器 / 解码失败）时返回原文件，交给后端兜底。
 */
async function compressImage(file) {
  if (!file || file.size <= COMPRESS_THRESHOLD_BYTES) return file
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') {
    return file
  }
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
    const width = Math.round(bitmap.width * scale)
    const height = Math.round(bitmap.height * scale)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      bitmap.close?.()
      return file
    }
    // 透明图（PNG/WebP）铺白底再转 JPEG，避免透明区域变黑
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, width, height)
    ctx.drawImage(bitmap, 0, 0, width, height)
    bitmap.close?.()
    const blob = await new Promise((resolve) => {
      canvas.toBlob((result) => resolve(result), 'image/jpeg', JPEG_QUALITY)
    })
    if (!blob || blob.size >= file.size) return file
    return new File([blob], file.name.replace(/\.(png|webp)$/i, '.jpg'), {
      type: 'image/jpeg',
      lastModified: Date.now(),
    })
  } catch {
    return file
  }
}

async function putToCos(uploadUrl, headers, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', uploadUrl, true)
    Object.entries(headers || {}).forEach(([key, value]) => {
      if (value != null) xhr.setRequestHeader(key, value)
    })
    if (typeof onProgress === 'function') {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new Error(`COS 直传失败（HTTP ${xhr.status}）`))
    }
    xhr.onerror = () => reject(new Error('COS 直传网络错误'))
    xhr.ontimeout = () => reject(new Error('COS 直传超时'))
    xhr.timeout = 120000
    xhr.send(file)
  })
}

/**
 * 尝试直传。成功返回后端 commit 的附件对象；不可用/失败返回 null。
 * 调用方拿到 null 时必须回退 multipart，不能把用户图片丢掉。
 */
export async function tryDirectImageUpload(file, { scope, orderId, onProgress } = {}) {
  if (!isUploadableImage(file)) return null
  if (file.size > MAX_UPLOAD_BYTES) return null
  let prepared = file
  try {
    prepared = await compressImage(file)
  } catch {
    prepared = file
  }
  if (prepared.size > MAX_UPLOAD_BYTES) return null

  let presign
  try {
    const response = await api.post('/uploads/presign', {
      scope,
      order_id: Number(orderId),
      filename: prepared.name || 'image.jpg',
      content_type: prepared.type,
      size: prepared.size,
    })
    presign = response.data
  } catch (err) {
    // 409 = 当前是本地存储，直传不可用；其它错误同样回退，不打扰用户
    return null
  }
  if (!presign?.upload_url || !presign?.key) return null

  try {
    await putToCos(presign.upload_url, presign.headers, prepared, onProgress)
  } catch {
    return null
  }

  try {
    const committed = await api.post('/uploads/commit', {
      scope,
      order_id: Number(orderId),
      key: presign.key,
      // 用压缩后的文件名：大图会被转成 JPEG，沿用原 .png 名会与对象后缀不一致
      name: prepared.name || file.name || 'image.jpg',
      content_type: prepared.type,
    })
    return committed.data
  } catch {
    return null
  }
}

export { compressImage, COMPRESS_THRESHOLD_BYTES, MAX_EDGE }
