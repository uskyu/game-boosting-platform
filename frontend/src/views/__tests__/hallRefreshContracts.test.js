import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')
const ordersStoreSource = readFileSync(new URL('../../stores/orders.js', import.meta.url), 'utf8')
const apiSource = readFileSync(new URL('../../utils/api.js', import.meta.url), 'utf8')

function slice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing start marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start, end)
}

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

// ── 大厅 P1b：摘要统计按需计算 + 429 退避 + 最大陈旧窗口 ──
describe('hall digest stats & 429 backoff contracts', () => {
  it('compiles the hall view', () => {
    compileView(hallSource, 'OrderHallView.vue')
  })

  it('requests stats on the first screen with include_stats=true', () => {
    const lifecycle = slice(hallSource, 'async function startHallLifecycle()', 'onMounted(() => {')
    expect(lifecycle).toMatch(/requestHallDigest\(\{ includeStats: true \}\)/)
    // 首屏摘要也必须计入统计刷新时刻，避免 2 秒兜底马上再打一次重聚合
    expect(lifecycle).toMatch(/lastHallStatsRefreshAt = 0/)
    expect(lifecycle).toMatch(/hallDigestRetryAt = 0/)
  })

  it('sends include_stats on every digest request and keeps stats across false responses', () => {
    const storeSlice = slice(ordersStoreSource, 'async function fetchHallDigest(', '\n  function setOrdersContext(')
    expect(storeSlice).toMatch(/const includeStats = options\.includeStats !== false/)
    expect(storeSlice).toMatch(/params: \{ include_stats: includeStats \}/)
    // 返回值契约：success/data/status/retryAfter/stale 都在（failed 也要带 retryAfter）
    expect(storeSlice).toMatch(/retryAfter: err\.retryAfter \?\? null/)

    const setStats = slice(ordersStoreSource, 'function setHallStats(', '// GET /orders/hall-digest')
    // 只有后端明确说 stats_included=true 才落库；false/缺字段一律保留上次有效值
    const guardIndex = setStats.indexOf('stats.stats_included !== true')
    const writeIndex = setStats.indexOf('hallStats.value = {')
    expect(guardIndex).toBeGreaterThan(-1)
    expect(writeIndex).toBeGreaterThan(guardIndex)
    expect(setStats.slice(guardIndex, writeIndex)).toMatch(/return/)
  })

  it('extracts Retry-After from the response header or structured detail', () => {
    expect(apiSource).toMatch(/response\.headers\?\.\[['\"]retry-after['\"]\]/)
    expect(apiSource).toMatch(/response\.data\?\.detail\?\.retry_after/)
    expect(apiSource).toMatch(/retryAfter: readRetryAfterSeconds\(error\)/)
  })

  it('backs off on digest 429 without escalating to a full list request', () => {
    const refresh = slice(hallSource, 'async function silentRefresh()', '// 新订单通知到达时')
    expect(hallSource).toMatch(/let hallDigestRetryAt = 0/)
    expect(refresh).toMatch(/const digestBackoffActive = nowMs < hallDigestRetryAt/)
    expect(refresh).toMatch(/if \(digestBackoffActive && !forcedFullReconcile\) return/)

    // 429 只设置退避、只对 404 标记摘要不可用（不能把限流升级成"每轮全量"）
    const request = slice(hallSource, 'async function requestHallDigest(', 'async function probeHallDigestKey(')
    expect(request).toMatch(/result\.status === 429/)
    expect(request).toMatch(/hallDigestRetryAt = Date\.now\(\) \+ hallDigestRetryDelayMs\(result\.retryAfter\)/)
    expect(request).toMatch(/hallDigestRetryAt = 0/)
    const probe = slice(hallSource, 'async function probeHallDigestKey(', 'function hallFullRefreshWithinWindow(')
    expect(probe).toMatch(/result\.status === 404\) hallDigestUnavailable = true/)
    expect(probe).not.toMatch(/429/)

    // 本轮探测 429 后必须在全量请求之前早退（到期前不探、不拉全量）
    const probeIndex = refresh.indexOf('const digestKey = await probeHallDigestKey()')
    const earlyReturn = refresh.indexOf('if (Date.now() < hallDigestRetryAt) return')
    const fullIndex = refresh.indexOf('await measureOrderFlowStage(')
    expect(probeIndex).toBeGreaterThan(-1)
    expect(earlyReturn).toBeGreaterThan(probeIndex)
    expect(fullIndex).toBeGreaterThan(earlyReturn)
    // 429 退避期早退发生在进入全量逻辑之前
    expect(refresh.indexOf('if (digestBackoffActive && !forcedFullReconcile) return'))
      .toBeLessThan(fullIndex)
  })

  it('retries probes after Retry-After expires and resets backoff on recovery', () => {
    const refresh = slice(hallSource, 'async function silentRefresh()', '// 新订单通知到达时')
    // 退避判定基于到期时刻，而不是一次性开关：到期后自动恢复正常探测
    expect(hallSource).toMatch(/function hallDigestRetryDelayMs\(retryAfter\)/)
    expect(hallSource).toMatch(/HALL_DIGEST_DEFAULT_RETRY_MS/)
    expect(refresh).toMatch(/Date\.now\(\) < hallDigestRetryAt/)
    // 摘要/全量成功都立即重置退避
    expect(slice(hallSource, 'async function requestHallDigest(', 'async function probeHallDigestKey('))
      .toMatch(/if \(result\.success && !result\.stale\) \{\s*hallDigestRetryAt = 0/)
    const record = slice(hallSource, 'async function recordHallFullRefreshSuccess(', 'function handleHallVisibility()')
    expect(record).toMatch(/lastHallFullRefreshAt = Date\.now\(\)/)
    expect(record).toMatch(/hallDigestRetryAt = 0/)
  })

  it('refreshes stats every 30 seconds even on the 2-second fallback', () => {
    expect(hallSource).toMatch(/const HALL_STATS_REFRESH_INTERVAL = 30_000/)
    expect(hallSource).toMatch(/let lastHallStatsRefreshAt = 0/)
    expect(hallSource).toMatch(/function hallStatsRefreshDue\(\) \{/)
    expect(hallSource).toMatch(/Date\.now\(\) - lastHallStatsRefreshAt >= HALL_STATS_REFRESH_INTERVAL/)

    // 2 秒兜底默认不带统计；只有统计到期的那一轮（probe 缺省判定）才 true
    const probe = slice(hallSource, 'async function probeHallDigestKey(', 'function hallFullRefreshWithinWindow(')
    expect(probe).toMatch(/options\.includeStats \?\? hallStatsRefreshDue\(\)/)
    const request = slice(hallSource, 'async function requestHallDigest(', 'async function probeHallDigestKey(')
    expect(request).toMatch(/if \(includeStats\) lastHallStatsRefreshAt = Date\.now\(\)/)

    // 全量对账轮次若统计到期，也要补一次带统计的摘要（列表不带顶部统计）
    const record = slice(hallSource, 'async function recordHallFullRefreshSuccess(', 'function handleHallVisibility()')
    expect(record).toMatch(/if \(!hallDigestUnavailable && hallStatsRefreshDue\(\)\) \{\s*await probeHallDigestKey\(\)/)

    // 兜底节奏仍是 2 秒（不能为了统计刷新改变轮询频率）
    expect(hallSource).toMatch(/const HALL_FALLBACK_INTERVAL = 2_000/)
  })

  it('forces a full reconcile after the 60-second stale window even during 429 backoff', () => {
    expect(hallSource).toMatch(/const HALL_DIGEST_MAX_STALE_MS = 60_000/)
    const refresh = slice(hallSource, 'async function silentRefresh()', '// 新订单通知到达时')
    expect(refresh).toMatch(/const forcedFullReconcile = digestBackoffActive\s*\n\s*&& nowMs - lastHallFullRefreshAt >= HALL_DIGEST_MAX_STALE_MS/)
    // 强制全量跳过摘要探测，直接进入全量；60 秒窗口以「成功全量」为基准
    expect(refresh).toMatch(/!forcedFullReconcile\s*\n\s*&& !hallDigestUnavailable/)
    expect(refresh).toMatch(/if \(!fullRateLimited\) succeeded = true/)
    expect(refresh).toMatch(/fullRefreshConfirmed = fullResult\?\.success !== false/)
    expect(refresh).toMatch(/if \(fullRefreshConfirmed\) await recordHallFullRefreshSuccess\(\)/)
  })

  it('backs off full-list 429 with Retry-After and keeps it out of the slow-request backoff', () => {
    const refresh = slice(hallSource, 'async function silentRefresh()', '// 新订单通知到达时')
    expect(refresh).toMatch(/const fullResult = await measureOrderFlowStage\(/)
    expect(refresh).toMatch(/fullRateLimited = fullResult\?\.status === 429/)
    expect(refresh).toMatch(/hallDigestRetryAt = Math\.max\(/)
    expect(refresh).toMatch(/hallDigestRetryDelayMs\(fullResult\.retryAfter\)/)
    // 429 不喂兜底慢请求退避（单独 backoff），其他结果保持原语义
    const finallyBlock = refresh.slice(refresh.indexOf('} finally {'))
    expect(finallyBlock).toMatch(/if \(!fullRateLimited\) \{\s*adjustHallFallbackInterval\(succeeded, fullRefreshElapsedMs\)/)
    // 429 那次不算成功全量，不推进 60 秒窗口
    expect(refresh).toMatch(/if \(!fullRateLimited\) \{\s*refreshHallRecentClaimsIfDue\(\)/)
  })

  it('keeps the old fallback for non-429 digest failures and 404 unavailability', () => {
    const refresh = slice(hallSource, 'async function silentRefresh()', '// 新订单通知到达时')
    // 非 429 的摘要失败：digestKey 为 null，落到全量（原有兜底）
    expect(refresh).toMatch(/!forcedFullReconcile\s*\n\s*&& !hallDigestUnavailable\s*\n\s*&& hallDigestBaseline !== null/)
    expect(refresh).not.toMatch(/hallDigestUnavailable = true/)
    const probe = slice(hallSource, 'async function probeHallDigestKey(', 'function hallFullRefreshWithinWindow(')
    expect(probe).toMatch(/if \(!result\.success\) \{/)
    expect(probe).toMatch(/result\.status === 404\) hallDigestUnavailable = true/)
  })
})
