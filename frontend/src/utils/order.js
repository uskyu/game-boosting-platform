export const ORDER_STATUS_META = {
  PENDING: {
    label: '待接单',
    badgeClass: 'badge-pending',
    description: '订单已发布，等待打手接手订单。',
  },
  LOCKED: {
    label: '进行中',
    badgeClass: 'badge-locked',
    description: '打手已接手订单，正在进行了。',
  },
  DELIVERED: {
    label: '待确认',
    badgeClass: 'badge-review',
    description: '打手已结束订单，等待老板确认。',
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
    label: '用户',
    badgeClass: 'badge-review',
  },
  BOOSTER: {
    label: '打手',
    badgeClass: 'badge-locked',
  },
  ADMIN: {
    label: '管理员',
    badgeClass: 'badge-approved',
  },
}

// claim（报名单）状态：CLAIMED=进行中 / DELIVERED=待审核 / SETTLED=已结算
export const ORDER_CLAIM_STATUS_META = {
  CLAIMED: { label: '进行中', tagClass: 'tag !bg-info-soft !text-info' },
  DELIVERED: { label: '待审核', tagClass: 'tag !bg-warning-soft !text-warning' },
  SETTLED: { label: '已结算', tagClass: 'tag !bg-success-soft !text-success' },
}

export function getClaimStatusMeta(status) {
  return ORDER_CLAIM_STATUS_META[status] || { label: status || '-', tagClass: 'tag' }
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
 * 返回人性化的状态标签，区分老板/打手视角。
 * 打手动线：接手订单 → 进行中 → 结束订单（提交汇报）→ 老板确认。
 * viewRole: 'owner' | 'booster'
 */
export function getHumanStatusLabel(status, serviceType, viewRole = 'owner') {
  const isBoosterView = viewRole === 'booster'

  if (isBoosterView) {
    const map = {
      PENDING: '待接手订单',
      LOCKED: '进行中',
      DELIVERED: '已结束，待老板确认',
      COMPLETED: '已完成',
      DISPUTED: '订单争议中',
      CANCELLED: '订单已取消',
    }
    return map[status] ?? getOrderStatusLabel(status)
  }

  const map = {
    PENDING: '等待打手接手订单',
    LOCKED: '打手进行中',
    DELIVERED: '打手已结束，请确认',
    COMPLETED: '订单完成了！',
    DISPUTED: '订单争议中',
    CANCELLED: '订单已取消',
  }
  return map[status] ?? getOrderStatusLabel(status)
}

/**
 * 返回状态对应的副标题，区分老板/打手视角。
 * viewRole: 'owner' | 'booster'
 */
export function getHumanStatusSubtitle(status, serviceType, viewRole = 'owner') {
  const isBoosterView = viewRole === 'booster'

  if (isBoosterView) {
    const map = {
      PENDING: '点击「接手订单」开始进行',
      LOCKED: '完成后点击「结束订单」并提交汇报',
      DELIVERED: '已结束订单，老板确认后收入计入余额',
      COMPLETED: '辛苦了，等待老板评价',
      DISPUTED: '平台正在介入处理',
      CANCELLED: '订单已取消',
    }
    return map[status] ?? ''
  }

  const map = {
    PENDING: '订单已发出，等待打手接手',
    LOCKED: '打手正在进行订单',
    DELIVERED: '核实打手的结束汇报，确认后完成结算',
    COMPLETED: '记得说说这次体验',
    DISPUTED: '平台正在介入处理',
    CANCELLED: '需要重新发一单吗？',
  }
  return map[status] ?? ''
}
