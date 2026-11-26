/**
 * 消息中心 / 会话列表 UI 合理化契约。
 *
 * 这里验证源码层面的关键约束，防止回退：
 * - 通知分页不再用 v-for 渲染全部页码，而是交给 CompactPagination；
 * - 消息中心头部用 tab-bar + ChatUnreadBadge，不再把原始未读数塞进 tab；
 * - 会话列表的“管理员”标签排除当前登录用户；
 * - 副标题过滤“未指定”等空段位噪音。
 */
import { readFileSync } from 'node:fs'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const messageCenterSource = readFileSync(
  new URL('../MessageCenterView.vue', import.meta.url),
  'utf8',
)
const notificationListSource = readFileSync(
  new URL('../NotificationListView.vue', import.meta.url),
  'utf8',
)
const conversationListSource = readFileSync(
  new URL('../../components/chat/ChatConversationList.vue', import.meta.url),
  'utf8',
)
const paginationComponentSource = readFileSync(
  new URL('../../components/CompactPagination.vue', import.meta.url),
  'utf8',
)

function compileSfc(source, filename) {
  const { descriptor, errors } = parse(source, { filename })
  expect(errors).toEqual([])

  const script = compileScript(descriptor, { id: filename })
  const template = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  })

  expect(template.errors).toEqual([])
}

function functionBody(source, startMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing function: ${startMarker}`).toBeGreaterThan(-1)
  const open = source.indexOf('{', start)
  expect(open).toBeGreaterThan(-1)
  let depth = 0
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1
    else if (source[index] === '}') {
      depth -= 1
      if (depth === 0) return source.slice(start, index + 1)
    }
  }
  throw new Error(`unbalanced body for ${startMarker}`)
}

describe('message center layout contracts', () => {
  it('compiles the touched SFCs', () => {
    compileSfc(messageCenterSource, 'MessageCenterView.vue')
    compileSfc(notificationListSource, 'NotificationListView.vue')
    compileSfc(conversationListSource, 'ChatConversationList.vue')
    compileSfc(paginationComponentSource, 'CompactPagination.vue')
  })

  it('uses CompactPagination instead of rendering every notification page', () => {
    expect(messageCenterSource).toContain('<CompactPagination')
    expect(messageCenterSource).toContain(':page="notifPage"')
    expect(messageCenterSource).toContain(':page-count="notifPageCount"')
    expect(messageCenterSource).toContain(':total="notifTotal"')
    expect(messageCenterSource).toContain('@change="loadNotifPage"')
    expect(messageCenterSource).not.toContain('v-for="p in notifPageCount"')

    expect(notificationListSource).toContain('<CompactPagination')
    expect(notificationListSource).toContain(':page="page"')
    expect(notificationListSource).toContain(':page-count="pageCount"')
    expect(notificationListSource).toContain('@change="loadPage"')
    expect(notificationListSource).not.toContain('v-for="p in pageCount"')
  })

  it('uses the segmented tab bar with the capped unread badge', () => {
    expect(messageCenterSource).toContain('class="tab-bar"')
    expect(messageCenterSource).toContain('tab-pill-active')
    expect(messageCenterSource).toContain("'tab-pill'")
    expect(messageCenterSource).toContain(':aria-pressed=')
    expect(messageCenterSource).toContain('<ChatUnreadBadge')
    expect(messageCenterSource).not.toContain('{{ chatUnread }}</span>')
    expect(messageCenterSource).not.toContain('{{ notifUnread }}</span>')
  })

  it('summarizes unread counts with formatCount and an all-read state', () => {
    expect(messageCenterSource).toContain('formatCount')
    expect(messageCenterSource).toContain('全部已读')
    expect(messageCenterSource).not.toContain('{{ chatUnread + notifUnread }} 条未读')
  })

  it('keeps CompactPagination overflow-safe and accessible', () => {
    expect(paginationComponentSource).toContain(
      'flex w-full min-w-0 flex-wrap items-center justify-center gap-1.5',
    )
    expect(paginationComponentSource).toContain('btn-secondary !px-3 !py-2')
    expect(paginationComponentSource).toContain('aria-label="分页"')
    expect(paginationComponentSource).toContain(':aria-current=')
    expect(paginationComponentSource).toContain('aria-label="上一页"')
    expect(paginationComponentSource).toContain('aria-label="下一页"')
    expect(paginationComponentSource).toContain('formatCount(total)')
    expect(paginationComponentSource).toContain('buildPageItems')
  })
})

describe('conversation list contracts', () => {
  it('excludes the current user from the admin badge', () => {
    const hasAdminBody = functionBody(conversationListSource, 'function hasAdmin(conversation)')

    expect(hasAdminBody).toContain('currentUserId.value')
    expect(hasAdminBody).toContain('isCurrentUserParticipant')
    expect(hasAdminBody).toContain('return false')
    expect(hasAdminBody).toContain("role === 'ADMIN'")
    expect(hasAdminBody).toContain("role_snapshot === 'ADMIN'")
    expect(functionBody(conversationListSource, 'function isCurrentUserParticipant'))
      .toContain('user_id')
  })

  it('filters placeholder ranks and appends the order id', () => {
    const subtitleBody = functionBody(conversationListSource, 'function getSubtitle(conversation)')
    const placeholderBlock = conversationListSource.match(
      /const RANK_PLACEHOLDERS = new Set\(\[[\s\S]*?\]\)/,
    )
    expect(placeholderBlock).not.toBeNull()

    expect(subtitleBody).toContain("'站内会话'")
    expect(subtitleBody).toContain("'代练订单'")
    expect(placeholderBlock[0]).toContain("'未指定'")
    expect(placeholderBlock[0]).toContain("'不限'")
    expect(placeholderBlock[0]).toContain("'—'")
    expect(placeholderBlock[0]).toContain("'-'")
    expect(subtitleBody).toContain('→')
    expect(subtitleBody).toContain('订单 #')
    expect(conversationListSource)
      .not.toContain('${conversation.order.current_rank} → ${conversation.order.target_rank}')
  })
})
