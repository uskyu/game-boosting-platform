import { describe, expect, it } from 'vitest'
import {
  CHAT_IMAGE_MAX_BYTES,
  chatImageFilesFromClipboard,
  chatImageFilesFromDataTransfer,
  isChatImageFile,
  validateChatImageFiles,
} from '@/utils/chatImage'

function file(type, size = 1024, name = 'image') {
  return { type, size, name }
}

describe('chatImage 图片筛选与校验', () => {
  it('只接受后端支持的四类图片', () => {
    expect(isChatImageFile(file('image/png'))).toBe(true)
    expect(isChatImageFile(file('image/jpeg'))).toBe(true)
    expect(isChatImageFile(file('image/gif'))).toBe(true)
    expect(isChatImageFile(file('image/webp'))).toBe(true)
    expect(isChatImageFile(file('application/pdf'))).toBe(false)
    expect(isChatImageFile(file('image/svg+xml'))).toBe(false)
    expect(isChatImageFile(null)).toBe(false)
  })

  it('从拖拽的 dataTransfer.files 里筛出图片，忽略其他文件', () => {
    const files = chatImageFilesFromDataTransfer({
      files: [file('image/png', 10, 'a.png'), file('text/plain', 10, 'a.txt'), file('image/webp', 10, 'b.webp')],
    })
    expect(files.map((item) => item.name)).toEqual(['a.png', 'b.webp'])
  })

  it('从剪贴板 items 里提取图片，兼容只有 files 的浏览器', () => {
    const itemFile = file('image/png', 10, 'paste.png')
    const fromItems = chatImageFilesFromClipboard({
      items: [
        { kind: 'string', getAsFile: () => null },
        { kind: 'file', getAsFile: () => itemFile },
      ],
      files: [],
    })
    expect(fromItems).toEqual([itemFile])

    const fallbackFile = file('image/jpeg', 10, 'fallback.jpg')
    const fromFiles = chatImageFilesFromClipboard({
      items: [],
      files: [fallbackFile],
    })
    expect(fromFiles).toEqual([fallbackFile])
  })

  it('校验空文件、类型、大小，并允许一次多张', () => {
    expect(validateChatImageFiles([])).toMatch(/仅支持/)
    expect(validateChatImageFiles([file('application/pdf')])).toMatch(/仅支持/)
    expect(validateChatImageFiles([file('image/png', 0)])).toMatch(/为空/)
    expect(validateChatImageFiles([file('image/png', CHAT_IMAGE_MAX_BYTES + 1)])).toMatch(/10MB/)
    expect(validateChatImageFiles([file('image/png'), file('image/jpeg')])).toBe('')
  })
})
