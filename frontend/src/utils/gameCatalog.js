import { getGameImage } from '@/data/gameImages'

export const GAME_CATEGORY_META = [
  { value: 'MOBA', label: '多人竞技', shortLabel: 'MOBA', icon: 'MB', description: '推塔、团战与节奏博弈', accent: '#00f0ff' },
  { value: 'FPS', label: '射击对抗', shortLabel: 'FPS', icon: 'FP', description: '枪法、搜打撤与战术推进', accent: '#f97316' },
  { value: 'RPG', label: '角色养成', shortLabel: 'RPG', icon: 'RP', description: '副本、材料、日常和任务代肝', accent: '#a855f7' },
  { value: 'RACING', label: '竞速冲榜', shortLabel: 'RACING', icon: 'RC', description: '排位冲刺与技巧训练', accent: '#22c55e' },
  { value: 'CARD', label: '卡牌策略', shortLabel: 'CARD', icon: 'CD', description: '构筑、上分与活动推进', accent: '#facc15' },
  { value: 'SPORTS', label: '体育棋牌', shortLabel: 'SPORTS', icon: 'SP', description: '球类竞技与棋牌对局', accent: '#38bdf8' },
  { value: 'STRATEGY', label: '策略经营', shortLabel: 'SLG', icon: 'SG', description: '开荒、控号与联盟推进', accent: '#fb7185' },
  { value: 'FIGHTING', label: '格斗动作', shortLabel: 'FIGHT', icon: 'FG', description: '副本、连招与角色练度', accent: '#ef4444' },
  { value: 'SURVIVAL', label: '生存派对', shortLabel: 'SURV', icon: 'SV', description: '吃鸡、建造与协作求生', accent: '#14b8a6' },
  { value: 'RHYTHM', label: '音游节奏', shortLabel: 'RHYTHM', icon: 'RY', description: '谱面代打与活动代肝', accent: '#ec4899' },
]

export const GAME_PLATFORM_META = {
  MOBILE: { label: '手游', shortLabel: 'MOBILE' },
  PC: { label: '端游', shortLabel: 'PC' },
  BOTH: { label: '双端', shortLabel: 'BOTH' },
}

function isPlaceholderAsset(url) {
  if (!url) {
    return false
  }

  return /placehold\.co|via\.placeholder\.com|dummyimage\.com/i.test(url)
}

export function getGameCategoryMeta(category) {
  return (
    GAME_CATEGORY_META.find((item) => item.value === category) || {
      value: category || 'UNKNOWN',
      label: category || '未分类',
      shortLabel: category || 'UNKNOWN',
      icon: 'GM',
      description: '更多游戏即将加入',
      accent: '#00f0ff',
    }
  )
}

export function getGamePlatformMeta(platform) {
  return GAME_PLATFORM_META[platform] || {
    label: platform || '未知平台',
    shortLabel: platform || 'UNKNOWN',
  }
}

export function getGamePlatformLabel(platform) {
  return getGamePlatformMeta(platform).label
}

export function resolveGameVisual(game) {
  const fallback = getGameImage(game?.name)
  const coverUrl = !isPlaceholderAsset(game?.cover_url) ? game?.cover_url : null
  const iconUrl = !isPlaceholderAsset(game?.icon_url) ? game?.icon_url : null

  return {
    hero: coverUrl || fallback.hero || game?.cover_url || null,
    icon: iconUrl || coverUrl || fallback.hero || game?.icon_url || game?.cover_url || null,
    color: game?.color_theme || fallback.color || '#00f0ff',
    gradient: fallback.gradient || 'from-cyan-500/20 to-blue-600/20',
  }
}

export function getGameServiceTypes(game) {
  return Array.isArray(game?.service_template?.service_types)
    ? game.service_template.service_types
    : []
}

export function buildGameSurfaceStyle(game) {
  const visual = resolveGameVisual(game)

  // 刮层走主题变量（--scrim-*，亮/暗各自调校），游戏图与品牌色保留为内容资产
  return {
    backgroundImage: visual.hero
      ? `linear-gradient(115deg, var(--scrim-strong) 0%, var(--scrim-mid) 55%, var(--scrim-soft) 100%), url('${visual.hero}')`
      : 'var(--surface-3)',
    backgroundPosition: 'center',
    backgroundSize: 'cover',
  }
}

export function buildAccentStyle(game) {
  const visual = resolveGameVisual(game)
  const color = visual.color

  // 游戏主题色小徽标（内容资产）：品牌色 8–15% 透明底，无发光
  return {
    borderColor: `${color}66`,
    background: `linear-gradient(135deg, ${color}26, ${color}12)`,
  }
}
