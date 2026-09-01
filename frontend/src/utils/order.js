export const ORDER_STATUS_META = {
  PENDING: {
    label: '待接单',
    badgeClass: 'badge-pending',
    description: '订单已发布，等待代练师接单。',
  },
  LOCKED: {
    label: '进行中',
    badgeClass: 'badge-locked',
    description: '已有代练师接单，服务正在推进。',
  },
  DELIVERED: {
    label: '待确认',
    badgeClass: 'badge-review',
    description: '代练已提交完成，等待客户确认。',
  },
  COMPLETED: {
    label: '已完成',
    badgeClass: 'badge-completed',
    description: '订单已正常完结。',
  },
  DISPUTED: {
    label: '争议中',
    badgeClass: 'badge-disputed',
    description: '订单已进入平台介入流程。',
  },
  CANCELLED: {
    label: '已取消',
    badgeClass: 'badge-cancelled',
    description: '订单已取消，不再继续执行。',
  },
}

export const ORDER_STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '待接单' },
  { value: 'LOCKED', label: '进行中' },
  { value: 'DELIVERED', label: '待确认' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'DISPUTED', label: '争议中' },
  { value: 'CANCELLED', label: '已取消' },
]

export const USER_ROLE_META = {
  USER: {
    label: '普通用户',
    badgeClass: 'badge-review',
  },
  BOOSTER: {
    label: '代练师',
    badgeClass: 'badge-locked',
  },
  ADMIN: {
    label: '管理员',
    badgeClass: 'badge-approved',
  },
}

export const APPLICATION_STATUS_META = {
  NONE: {
    label: '未提交',
    badgeClass: 'badge-cancelled',
    description: '还没有提交代练师认证申请。',
  },
  PENDING: {
    label: '待审核',
    badgeClass: 'badge-review',
    description: '申请已提交，等待管理员审核。',
  },
  APPROVED: {
    label: '已通过',
    badgeClass: 'badge-approved',
    description: '审核已通过，可以开始接单。',
  },
  REJECTED: {
    label: '已拒绝',
    badgeClass: 'badge-rejected',
    description: '申请未通过，可根据备注完善后再次提交。',
  },
}

export function getOrderStatusMeta(status) {
  return ORDER_STATUS_META[status] || {
    label: status || '未知状态',
    badgeClass: 'badge-cancelled',
    description: '当前状态暂无说明。',
  }
}

export function getOrderStatusLabel(status) {
  return getOrderStatusMeta(status).label
}

export function getOrderStatusBadgeClass(status) {
  return getOrderStatusMeta(status).badgeClass
}

export function getUserRoleMeta(role) {
  return USER_ROLE_META[role] || {
    label: role || '未知角色',
    badgeClass: 'badge-cancelled',
  }
}

export function getUserRoleLabel(role) {
  return getUserRoleMeta(role).label
}

export function getApplicationStatusMeta(status) {
  return APPLICATION_STATUS_META[status] || APPLICATION_STATUS_META.NONE
}

/**
 * 返回人性化的状态标签，区分代练/陪玩场景和老板/打手视角。
 * serviceType: '代练' | '陪玩' | '教学' | 其他
 * viewRole: 'owner' | 'booster'
 */
export function getHumanStatusLabel(status, serviceType, viewRole = 'owner') {
  const isBoost = serviceType === '代练'
  const isBoosterView = viewRole === 'booster'

  if (isBoosterView) {
    const map = {
      PENDING: '等待接单',
      LOCKED: isBoost ? '上号中' : '陪玩中',
      DELIVERED: '等待老板确认',
      COMPLETED: '已完成',
      DISPUTED: '订单争议中',
      CANCELLED: '订单已取消',
    }
    return map[status] ?? getOrderStatusLabel(status)
  }

  const map = {
    PENDING: isBoost ? '等待代练接单' : '等待陪玩接单',
    LOCKED: isBoost ? '代练上号中' : '陪玩进行中',
    DELIVERED: isBoost ? '代练已完成，请确认' : '陪玩已结束，请确认',
    COMPLETED: isBoost ? '代练完成了！' : '这局打完了！',
    DISPUTED: '订单争议中',
    CANCELLED: '订单已取消',
  }
  return map[status] ?? getOrderStatusLabel(status)
}

/**
 * 返回状态对应的副标题，区分代练/陪玩场景和老板/打手视角。
 * viewRole: 'owner' | 'booster'
 */
export function getHumanStatusSubtitle(status, serviceType, viewRole = 'owner') {
  const isBoost = serviceType === '代练'
  const isBoosterView = viewRole === 'booster'

  if (isBoosterView) {
    const map = {
      PENDING: '可以先和老板聊聊再决定',
      LOCKED: isBoost ? '正在帮老板上分' : '正在陪老板打',
      DELIVERED: '已提交完成，等老板确认就能收款了',
      COMPLETED: '辛苦了，等待老板评价',
      DISPUTED: '平台正在介入处理',
      CANCELLED: '订单已取消',
    }
    return map[status] ?? ''
  }

  const map = {
    PENDING: isBoost ? '需求已发出，代练们正在看' : '需求已发出，陪玩们正在看',
    LOCKED: isBoost ? '代练正在使用你的账号上分' : '陪玩已就位，一起开黑吧',
    DELIVERED: isBoost ? '代练已完成，确认没问题就点确认吧' : '陪玩已结束，确认没问题就点确认吧',
    COMPLETED: '记得说说这次体验',
    DISPUTED: '平台正在介入处理',
    CANCELLED: '需要重新找吗？',
  }
  return map[status] ?? ''
}
