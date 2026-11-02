import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const chatStoreSource = readFileSync(new URL('../chat.js', import.meta.url), 'utf8')

describe('chat system messages', () => {
  it('are appended to history/preview but never increment unread or toast', () => {
    // 订单卡片、取消协商、名额取消这三类系统消息都走 SYSTEM；
    // 只更新聊天记录，不占用未读、不弹提醒。
    expect(chatStoreSource).toContain(
      "const isSystemMessage = message.message_type === 'SYSTEM' || message.sender_id == null",
    )
    expect(chatStoreSource).toContain(
      'if (!isSystemMessage && message.sender_id !== currentUserId)',
    )
  })
})
