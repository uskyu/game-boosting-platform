/**
 * 会话标题解析。
 *
 * 历史问题：标题直接拼接 other_participants，三人订单会话（客户 + 打手 +
 * 客服）会把 ADMIN 也拼进去；other_participants 被空数组覆盖时标题又退化
 * 成“订单 #id”。这里统一按“当前视角的对方”解析：
 *   1. 后端 counterpart（已按订单角色选人）；
 *   2. 订单会话按 order.user_id / booster_id 选对方（管理员视角优先下单方）；
 *   3. other_participants / participants 排除自己，并优先非 ADMIN；
 *   4. 只有客服可展示时再退回客服，最后才用订单号 / 会话号兜底。
 */

function normalizeId(value) {
  if (value === null || value === undefined || value === '') {
    return null
  }
  const id = Number(value)
  return Number.isFinite(id) ? id : null
}

function isSameUser(left, right) {
  const leftId = normalizeId(left)
  const rightId = normalizeId(right)
  return leftId !== null && rightId !== null && leftId === rightId
}

function isAdminRole(role) {
  return String(role || '').toUpperCase() === 'ADMIN'
}

function isAdminUser(user) {
  return isAdminRole(user?.role)
}

function isAdminParticipant(participant) {
  return isAdminUser(participant?.user) || isAdminRole(participant?.role_snapshot)
}

function hasText(value) {
  return typeof value === 'string' && value.trim().length > 0
}

function hasUsername(user) {
  return hasText(user?.username)
}

function findOtherUser(conversation, userId) {
  if (normalizeId(userId) === null) {
    return null
  }
  return (conversation?.other_participants || [])
    .find((user) => isSameUser(user?.id, userId)) || null
}

function findParticipantUser(conversation, userId) {
  if (normalizeId(userId) === null) {
    return null
  }
  const participant = (conversation?.participants || []).find((item) => (
    isSameUser(item?.user_id ?? item?.user?.id, userId)
  ))
  return participant?.user || null
}

function buildOrderUsernameCandidate(order, preferredId) {
  if (isSameUser(preferredId, order?.user_id) && hasText(order?.user_username)) {
    return { id: order.user_id, username: order.user_username }
  }
  if (isSameUser(preferredId, order?.booster_id) && hasText(order?.booster_username)) {
    return { id: order.booster_id, username: order.booster_username }
  }
  return null
}

function resolveOrderCounterpart(conversation, currentUserId) {
  const order = conversation?.order
  // 未拿到当前用户 id 时无法判断订单视角，交给 other_participants 兜底
  //（后端返回的 other_participants 本身已排除当前用户）。
  if (!order || normalizeId(currentUserId) === null) {
    return null
  }

  let preferredId = order.user_id
  if (isSameUser(currentUserId, order.user_id)) {
    // 下单方视角：对方是接单打手（未接单时由参与者列表兜底）
    preferredId = order.booster_id
  } else if (isSameUser(currentUserId, order.booster_id)) {
    // 打手视角：对方是下单方
    preferredId = order.user_id
  }
  // 其他视角（管理员/客服）：优先下单方

  if (normalizeId(preferredId) === null) {
    return null
  }

  const fromOthers = findOtherUser(conversation, preferredId)
  if (hasUsername(fromOthers)) {
    return fromOthers
  }

  const fromParticipants = findParticipantUser(conversation, preferredId)
  if (hasUsername(fromParticipants)) {
    return fromParticipants
  }

  return buildOrderUsernameCandidate(order, preferredId)
}

/**
 * 返回当前视角下应展示的对方用户列表。
 * @param {object | null | undefined} conversation 会话响应对象
 * @param {number | string | null | undefined} currentUserId 当前登录用户 ID
 * @returns {Array<object>} 用户 brief 列表（最多一个普通对方；客服仅在无其他对象时兜底）
 */
export function resolveCounterpartUsers(conversation, currentUserId) {
  if (!conversation) {
    return []
  }

  const currentId = normalizeId(currentUserId)

  // 1. 后端已经按视角选好对方，直接采用（排除异常情况下返回自己）
  const counterpart = conversation.counterpart
  if (hasUsername(counterpart) && !isSameUser(counterpart?.id, currentId)) {
    return [counterpart]
  }

  // 2. 订单会话按订单角色选对方：老板 ↔ 打手，管理员视角优先下单方
  const orderCounterpart = resolveOrderCounterpart(conversation, currentId)
  if (hasUsername(orderCounterpart)) {
    return [orderCounterpart]
  }

  const otherUsers = (conversation.other_participants || [])
    .filter((user) => user && !isSameUser(user?.id, currentId))

  // 3. 其他参与者优先非 ADMIN，避免标题拼出“Lin / 管理员”
  const otherNonAdmins = otherUsers.filter((user) => !isAdminUser(user))
  if (otherNonAdmins.length) {
    return otherNonAdmins
  }

  const participantUsers = (conversation.participants || [])
    .filter((participant) => (
      participant
      && !isSameUser(participant.user_id ?? participant.user?.id, currentId)
      && participant.user
    ))

  const participantNonAdmins = participantUsers
    .filter((participant) => !isAdminParticipant(participant))
    .map((participant) => participant.user)
    .filter(hasUsername)
  if (participantNonAdmins.length) {
    return participantNonAdmins
  }

  // 4. 只有客服参与（如“联系管理员”私聊）时仍显示客服名称
  const otherFallback = otherUsers.filter(hasUsername)
  if (otherFallback.length) {
    return otherFallback
  }

  return participantUsers.map((participant) => participant.user).filter(hasUsername)
}

/**
 * 会话标题：对方用户名 / 订单号 / 会话号 / 通用兜底。
 */
export function resolveConversationTitle(conversation, currentUserId) {
  const names = []
  const seen = new Set()

  resolveCounterpartUsers(conversation, currentUserId).forEach((user) => {
    const name = typeof user?.username === 'string' ? user.username.trim() : ''
    if (!name || seen.has(name)) {
      return
    }
    seen.add(name)
    names.push(name)
  })

  if (names.length) {
    return names.join(' / ')
  }

  if (conversation?.order?.id !== null && conversation?.order?.id !== undefined) {
    return `订单 #${conversation.order.id}`
  }

  if (conversation?.id !== null && conversation?.id !== undefined) {
    return `会话 #${conversation.id}`
  }

  return '聊天会话'
}
