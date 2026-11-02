import { describe, expect, it } from 'vitest'

import { resolveConversationTitle, resolveCounterpartUsers } from '../chat'

const owner = { id: 1, username: 'Lin', role: 'USER' }
const booster = { id: 2, username: 'BoostKing', role: 'BOOSTER' }
const admin = { id: 3, username: '平台客服', role: 'ADMIN' }

function orderConversation(overrides = {}) {
  return {
    id: 100,
    type: 'ORDER',
    order: {
      id: 9,
      user_id: owner.id,
      booster_id: booster.id,
      user_username: owner.username,
      booster_username: booster.username,
    },
    participants: [
      { user_id: owner.id, role_snapshot: 'USER', user: owner },
      { user_id: booster.id, role_snapshot: 'BOOSTER', user: booster },
      { user_id: admin.id, role_snapshot: 'ADMIN', user: admin },
    ],
    other_participants: [booster, admin],
    counterpart: null,
    ...overrides,
  }
}

describe('resolveCounterpartUsers', () => {
  it('三人订单会话：老板视角只取打手，不拼客服', () => {
    const conversation = orderConversation()

    expect(resolveCounterpartUsers(conversation, owner.id)).toEqual([booster])
    expect(resolveConversationTitle(conversation, owner.id)).toBe('BoostKing')
  })

  it('三人订单会话：管理员视角优先下单方，只显示老板', () => {
    const conversation = orderConversation()

    expect(resolveCounterpartUsers(conversation, admin.id)).toEqual([owner])
    expect(resolveConversationTitle(conversation, admin.id)).toBe('Lin')
  })

  it('优先使用后端 counterpart（字符串用户 id 也能匹配）', () => {
    const conversation = orderConversation({
      counterpart: booster,
      other_participants: [],
    })

    expect(resolveCounterpartUsers(conversation, '1')).toEqual([booster])
    expect(resolveConversationTitle(conversation, '1')).toBe('BoostKing')
  })

  it('other_participants 为空时从 participants 找对方，且不显示自己', () => {
    const conversation = orderConversation({
      counterpart: null,
      other_participants: [],
    })

    expect(resolveCounterpartUsers(conversation, owner.id)).toEqual([booster])
    expect(resolveConversationTitle(conversation, owner.id)).toBe('BoostKing')
    // 客服自己在会话里时，不会把自己当作对方
    expect(resolveCounterpartUsers(conversation, admin.id)).toEqual([owner])
  })

  it('没有参与者用户对象时用订单里的 user_username / booster_username 兜底', () => {
    const conversation = {
      id: 101,
      type: 'ORDER',
      order: {
        id: 9,
        user_id: owner.id,
        booster_id: booster.id,
        user_username: owner.username,
        booster_username: booster.username,
      },
      participants: [{ user_id: owner.id, role_snapshot: 'USER', user: owner }],
      other_participants: [],
      counterpart: null,
    }

    expect(resolveConversationTitle(conversation, owner.id)).toBe('BoostKing')
    expect(resolveConversationTitle(conversation, booster.id)).toBe('Lin')
  })

  it('订单会话无对方可取时兜底“订单 #id”', () => {
    const conversation = {
      id: 102,
      type: 'ORDER',
      order: { id: 9, user_id: owner.id, booster_id: null },
      participants: [{ user_id: owner.id, role_snapshot: 'USER', user: owner }],
      other_participants: [],
      counterpart: null,
    }

    expect(resolveCounterpartUsers(conversation, owner.id)).toEqual([])
    expect(resolveConversationTitle(conversation, owner.id)).toBe('订单 #9')
  })

  it('私聊取 other_participants 中的对方', () => {
    const conversation = {
      id: 200,
      type: 'PRIVATE',
      participants: [
        { user_id: owner.id, role_snapshot: 'USER', user: owner },
        { user_id: booster.id, role_snapshot: 'BOOSTER', user: booster },
      ],
      other_participants: [booster],
      counterpart: null,
    }

    expect(resolveConversationTitle(conversation, owner.id)).toBe('BoostKing')
  })

  it('联系管理员的私聊仍显示客服名称，不退化成会话号', () => {
    const conversation = {
      id: 201,
      type: 'PRIVATE',
      participants: [
        { user_id: owner.id, role_snapshot: 'USER', user: owner },
        { user_id: admin.id, role_snapshot: 'ADMIN', user: admin },
      ],
      other_participants: [admin],
      counterpart: null,
    }

    expect(resolveConversationTitle(conversation, owner.id)).toBe('平台客服')
  })

  it('other_participants 缺失时用 participants 兜底', () => {
    const conversation = {
      id: 202,
      type: 'PRIVATE',
      participants: [
        { user_id: owner.id, role_snapshot: 'USER', user: owner },
        { user_id: booster.id, role_snapshot: 'BOOSTER', user: booster },
      ],
      other_participants: undefined,
      counterpart: null,
    }

    expect(resolveConversationTitle(conversation, owner.id)).toBe('BoostKing')
  })

  it('最终兜底：会话 #id / 聊天会话', () => {
    expect(resolveConversationTitle({ id: 5 }, null)).toBe('会话 #5')
    expect(resolveConversationTitle({}, null)).toBe('聊天会话')
    expect(resolveConversationTitle(null, 1)).toBe('聊天会话')
  })
})
