// 聊天图片上传统一限制：与后端 send_image_message 的白名单/大小上限保持一致。
// 拖拽和粘贴拿到的是系统文件，不能只依赖 <input accept> 的界面过滤。
export const CHAT_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
export const CHAT_IMAGE_ACCEPT = CHAT_IMAGE_TYPES.join(',')
export const CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024

const CHAT_IMAGE_TYPE_SET = new Set(CHAT_IMAGE_TYPES)

export function isChatImageFile(file) {
  return Boolean(file && CHAT_IMAGE_TYPE_SET.has(file.type))
}

export function collectChatImageFiles(files) {
  return Array.from(files || []).filter(isChatImageFile)
}

export function chatImageFilesFromDataTransfer(dataTransfer) {
  return collectChatImageFiles(dataTransfer?.files)
}

export function chatImageFilesFromClipboard(clipboardData) {
  if (!clipboardData) {
    return []
  }

  const fromItems = Array.from(clipboardData.items || [])
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile?.())
    .filter(isChatImageFile)

  if (fromItems.length) {
    return fromItems
  }

  return collectChatImageFiles(clipboardData.files)
}

export function validateChatImageFiles(files) {
  const list = Array.from(files || [])
  if (!list.length) {
    return '仅支持 jpg、png、gif、webp 图片'
  }

  for (const file of list) {
    if (!isChatImageFile(file)) {
      return '仅支持 jpg、png、gif、webp 图片'
    }
    if (!file.size) {
      return '图片内容为空'
    }
    if (file.size > CHAT_IMAGE_MAX_BYTES) {
      return '图片大小不能超过10MB'
    }
  }

  return ''
}
