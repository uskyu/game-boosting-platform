/**
 * Centralized game image data.
 * Each game has an array of hero images; one is picked at random per session.
 * Keys MUST match the exact game name in the database.
 *
 * NOTE: only 5 games currently have local hero assets; the rest use an empty
 * pool and will fall back to their gradient placeholder until proper
 * high-resolution artwork is added.
 */

/* ── multi-image games (assets present) ── */
import honorOfKings1 from '@/assets/images/games/honor-of-kings/1.jpg'
import honorOfKings2 from '@/assets/images/games/honor-of-kings/2.jpg'
import honorOfKings3 from '@/assets/images/games/honor-of-kings/3.jpg'
import honorOfKings4 from '@/assets/images/games/honor-of-kings/4.jpg'
import honorOfKings5 from '@/assets/images/games/honor-of-kings/5.jpg'

import genshin1 from '@/assets/images/games/genshin/1.jpg'
import genshin2 from '@/assets/images/games/genshin/2.jpg'
import genshin3 from '@/assets/images/games/genshin/3.jpg'
import genshin4 from '@/assets/images/games/genshin/4.jpg'
import genshin5 from '@/assets/images/games/genshin/5.png'

import valorant1 from '@/assets/images/games/valorant/1.jpg'
import valorant2 from '@/assets/images/games/valorant/2.jpg'
import valorant3 from '@/assets/images/games/valorant/3.jpg'
import valorant4 from '@/assets/images/games/valorant/4.jpg'

import deltaForce1 from '@/assets/images/games/delta-force/1.jpg'
import deltaForce2 from '@/assets/images/games/delta-force/2.jpg'
import deltaForce3 from '@/assets/images/games/delta-force/3.jpg'
import deltaForce4 from '@/assets/images/games/delta-force/4.jpg'
import deltaForce5 from '@/assets/images/games/delta-force/5.jpg'

import tft2 from '@/assets/images/games/tft/2.jpg'
import tft3 from '@/assets/images/games/tft/3.jpg'
import tft4 from '@/assets/images/games/tft/4.jpg'
import tft5 from '@/assets/images/games/tft/5.jpg'

import pubgMobile1 from '@/assets/images/games/pubg-mobile/1.jpg'
import pubgMobile2 from '@/assets/images/games/pubg-mobile/2.jpg'

import dota2_1 from '@/assets/images/games/dota2/1.jpg'

import lol1 from '@/assets/images/games/lol/1.jpg'
import lol2 from '@/assets/images/games/lol/2.jpg'
import lol3 from '@/assets/images/games/lol/3.jpg'

import lolMobile1 from '@/assets/images/games/lol-mobile/1.jpg'
import lolMobile2 from '@/assets/images/games/lol-mobile/2.jpg'
import lolMobile3 from '@/assets/images/games/lol-mobile/3.jpg'

/* ── helpers ── */

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)]
}

const sessionPicks = new Map()

function sessionRandom(key, arr) {
  if (!sessionPicks.has(key)) {
    sessionPicks.set(key, pickRandom(arr))
  }
  return sessionPicks.get(key)
}

/* ── master registry: key = exact DB game name ──
 * Empty pool = no hero asset yet, renders gradient placeholder only.
 */

const REGISTRY = {
  // MOBA
  '王者荣耀':            { pool: [honorOfKings1, honorOfKings2, honorOfKings3, honorOfKings4, honorOfKings5], color: '#ff6b2b', gradient: 'from-orange-500/20 to-red-600/20' },
  '英雄联盟':            { pool: [lol1, lol2, lol3], color: '#c8aa6e', gradient: 'from-yellow-600/20 to-amber-700/20' },
  '英雄联盟手游':        { pool: [lolMobile1, lolMobile2, lolMobile3], color: '#c8aa6e', gradient: 'from-yellow-600/20 to-amber-700/20' },
  'DOTA2':               { pool: [dota2_1], color: '#c23c2a', gradient: 'from-red-700/20 to-orange-800/20' },
  '曙光英雄':            { pool: [], color: '#ff9900', gradient: 'from-amber-500/20 to-orange-600/20' },
  '决战！平安京':        { pool: [], color: '#e74c3c', gradient: 'from-red-500/20 to-pink-600/20' },

  // FPS
  '和平精英':            { pool: [pubgMobile1, pubgMobile2], color: '#f5c518', gradient: 'from-yellow-500/20 to-amber-600/20' },
  'CS2':                 { pool: [], color: '#de9b35', gradient: 'from-amber-500/20 to-yellow-600/20' },
  '穿越火线':            { pool: [], color: '#ff4400', gradient: 'from-orange-600/20 to-red-700/20' },
  '穿越火线手游':        { pool: [], color: '#ff4400', gradient: 'from-orange-600/20 to-red-700/20' },
  '三角洲行动':          { pool: [deltaForce1, deltaForce2, deltaForce3, deltaForce4, deltaForce5], color: '#f5c518', gradient: 'from-yellow-500/20 to-amber-600/20' },
  '无畏契约 (VALORANT)': { pool: [valorant1, valorant2, valorant3, valorant4], color: '#ff4655', gradient: 'from-red-500/20 to-rose-600/20' },
  '暗区突围':            { pool: [], color: '#4a6741', gradient: 'from-green-700/20 to-emerald-800/20' },

  // RPG
  '原神':                { pool: [genshin1, genshin2, genshin3, genshin4, genshin5], color: '#a78bfa', gradient: 'from-violet-500/20 to-purple-600/20' },
  '崩坏：星穹铁道':      { pool: [], color: '#6366f1', gradient: 'from-indigo-500/20 to-violet-600/20' },
  '绝区零':              { pool: [], color: '#f97316', gradient: 'from-orange-500/20 to-amber-600/20' },
  '鸣潮':                { pool: [], color: '#06b6d4', gradient: 'from-cyan-500/20 to-teal-600/20' },
  '梦幻西游':            { pool: [], color: '#fbbf24', gradient: 'from-yellow-400/20 to-amber-500/20' },
  '逆水寒':              { pool: [], color: '#8b5cf6', gradient: 'from-violet-500/20 to-purple-600/20' },
  '燕云十六声':          { pool: [], color: '#a3866a', gradient: 'from-amber-700/20 to-stone-600/20' },

  // RACING
  'QQ飞车手游':          { pool: [], color: '#3b82f6', gradient: 'from-blue-500/20 to-indigo-600/20' },
  '跑跑卡丁车手游':      { pool: [], color: '#f43f5e', gradient: 'from-rose-500/20 to-pink-600/20' },
  '极品飞车：集结':      { pool: [], color: '#0ea5e9', gradient: 'from-sky-500/20 to-blue-600/20' },
  '巅峰极速':            { pool: [], color: '#ef4444', gradient: 'from-red-500/20 to-orange-600/20' },
  '王牌竞速':            { pool: [], color: '#8b5cf6', gradient: 'from-violet-500/20 to-indigo-600/20' },

  // CARD
  '金铲铲之战':          { pool: [tft2, tft3, tft4, tft5], color: '#00a3ff', gradient: 'from-blue-500/20 to-cyan-600/20' },
  '炉石传说':            { pool: [], color: '#f59e0b', gradient: 'from-amber-500/20 to-yellow-600/20' },
  '阴阳师':              { pool: [], color: '#dc2626', gradient: 'from-red-600/20 to-rose-700/20' },
  '三国杀':              { pool: [], color: '#b91c1c', gradient: 'from-red-700/20 to-rose-800/20' },
  '游戏王：决斗链接':    { pool: [], color: '#eab308', gradient: 'from-yellow-500/20 to-amber-600/20' },
  '龙息：神寂':          { pool: [], color: '#7c3aed', gradient: 'from-purple-600/20 to-violet-700/20' },

  // SPORTS
  'FIFA Online 4':       { pool: [], color: '#16a34a', gradient: 'from-green-600/20 to-emerald-700/20' },
  '实况足球手游':        { pool: [], color: '#2563eb', gradient: 'from-blue-600/20 to-indigo-700/20' },
  'NBA2K Online 2':      { pool: [], color: '#ea580c', gradient: 'from-orange-600/20 to-red-700/20' },
  '欢乐斗地主':          { pool: [], color: '#dc2626', gradient: 'from-red-600/20 to-rose-700/20' },
  '欢乐麻将':            { pool: [], color: '#059669', gradient: 'from-emerald-600/20 to-green-700/20' },

  // STRATEGY
  '率土之滨':            { pool: [], color: '#92400e', gradient: 'from-amber-800/20 to-yellow-900/20' },
  '三国志战略版':        { pool: [], color: '#b45309', gradient: 'from-amber-700/20 to-orange-800/20' },
  '三国志・战棋版':      { pool: [], color: '#a16207', gradient: 'from-yellow-700/20 to-amber-800/20' },
  '文明与征服':          { pool: [], color: '#0d9488', gradient: 'from-teal-600/20 to-cyan-700/20' },
  '万国觉醒':            { pool: [], color: '#7c3aed', gradient: 'from-purple-600/20 to-violet-700/20' },
  '重返帝国':            { pool: [], color: '#ca8a04', gradient: 'from-yellow-600/20 to-amber-700/20' },

  // FIGHTING
  '地下城与勇士':        { pool: [], color: '#3b82f6', gradient: 'from-blue-500/20 to-indigo-600/20' },
  '地下城与勇士：起源':  { pool: [], color: '#2563eb', gradient: 'from-blue-600/20 to-indigo-700/20' },
  '拳皇命运':            { pool: [], color: '#dc2626', gradient: 'from-red-600/20 to-rose-700/20' },
  '街霸：对决':          { pool: [], color: '#ea580c', gradient: 'from-orange-600/20 to-red-700/20' },
  '火影忍者手游':        { pool: [], color: '#f97316', gradient: 'from-orange-500/20 to-amber-600/20' },
  '鬼泣：巅峰之战':      { pool: [], color: '#991b1b', gradient: 'from-red-800/20 to-rose-900/20' },

  // SURVIVAL
  '蛋仔派对':            { pool: [], color: '#f472b6', gradient: 'from-pink-400/20 to-rose-500/20' },
  '明日之后':            { pool: [], color: '#64748b', gradient: 'from-slate-500/20 to-gray-600/20' },
  '永劫无间':            { pool: [], color: '#1e293b', gradient: 'from-slate-800/20 to-gray-900/20' },
  '香肠派对':            { pool: [], color: '#facc15', gradient: 'from-yellow-400/20 to-amber-500/20' },
  '方舟：生存进化':      { pool: [], color: '#0f766e', gradient: 'from-teal-700/20 to-emerald-800/20' },
  '黎明觉醒':            { pool: [], color: '#b45309', gradient: 'from-amber-700/20 to-orange-800/20' },

  // RHYTHM
  'Phigros':             { pool: [], color: '#06b6d4', gradient: 'from-cyan-500/20 to-teal-600/20' },
  '节奏大师':            { pool: [], color: '#8b5cf6', gradient: 'from-violet-500/20 to-purple-600/20' },
  '世界计划缤纷舞台':    { pool: [], color: '#ec4899', gradient: 'from-pink-500/20 to-rose-600/20' },
  'Arcaea':              { pool: [], color: '#6366f1', gradient: 'from-indigo-500/20 to-violet-600/20' },
  '喵斯快跑':            { pool: [], color: '#f43f5e', gradient: 'from-rose-500/20 to-pink-600/20' },
}

/* ── build GAME_IMAGES with lazy hero getter ── */

export const GAME_IMAGES = {}
const GAME_HERO_POOLS = {}

for (const [name, cfg] of Object.entries(REGISTRY)) {
  GAME_HERO_POOLS[name] = cfg.pool
  GAME_IMAGES[name] = {
    heroPool: cfg.pool,
    get hero() { return cfg.pool.length ? sessionRandom(name, cfg.pool) : null },
    color: cfg.color,
    gradient: cfg.gradient,
  }
}

// 录制演示视频期间，所有非游戏页面强制使用和平精英本地图，避免外链失效或空白页。
export const PAGE_BACKGROUNDS = {
  hero: pubgMobile1,
  login: pubgMobile1,
  register: pubgMobile1,
  notFound: pubgMobile1,
}

export function getGameImage(gameName) {
  return GAME_IMAGES[gameName] || {
    hero: null,
    color: '#00f0ff',
    gradient: 'from-cyan-500/20 to-blue-600/20',
  }
}

export function getGameHeroPool(gameName) {
  return GAME_HERO_POOLS[gameName] || []
}

export function refreshGameHero(gameName) {
  const pool = GAME_HERO_POOLS[gameName]
  if (pool && pool.length) {
    sessionPicks.set(gameName, pickRandom(pool))
  }
  return sessionPicks.get(gameName) || null
}

export function onImgError(event) {
  const el = event.target
  el.style.display = 'none'
  if (el.parentElement) {
    el.parentElement.style.background = 'linear-gradient(135deg, #12121a, #1a1a2e)'
  }
}
