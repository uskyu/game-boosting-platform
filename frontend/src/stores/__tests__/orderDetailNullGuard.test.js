/**
 * 订单详情页白屏回归（老板 2026-10-18 线上反馈）。
 *
 * 事故：`AfterSaleStatusModal` 直接写 `:order-id="order.id"`，却曾被放在
 * `v-if="order"` 保护范围**之外**。订单还没加载完 / 拉取失败 / 订单不存在时
 * `order` 是 null，求值抛 `Cannot read properties of null (reading 'id')`，
 * 整个模板渲染中断 —— 表现为整页白屏，连「订单加载失败 / 重新加载」分支
 * 都渲染不出来。
 *
 * 这里用源码扫描锁住结构：任何 `order.id` 的模板引用都必须落在 `order` 的
 * 保护范围里。真正的行为验证在浏览器走查里做（api 失败时能看到重试页）。
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  new URL('../../views/OrderDetail.vue', import.meta.url),
  'utf8',
)

/** 取出 `<template>` 根块的正文（去掉脚本部分）。 */
function templateBody() {
  const start = source.indexOf('<template>')
  return source.slice(start)
}

/** 从某个带 `v-if="order"` 的分支起点，找到它对应的结束位置。 */
function guardedRanges() {
  const body = templateBody()
  const ranges = []
  const guardRe = /<template\s+v-(?:else-)?if="order"/g
  let m
  while ((m = guardRe.exec(body)) !== null) {
    // 从 guard 起点做深度匹配，找到配对的 </template>
    let depth = 0
    let i = m.index
    const tagRe = /<template\b|<\/template>/g
    tagRe.lastIndex = m.index
    let t
    let end = body.length
    while ((t = tagRe.exec(body)) !== null) {
      if (t[0].startsWith('</')) {
        depth -= 1
        if (depth === 0) {
          end = t.index + t[0].length
          break
        }
      } else {
        depth += 1
        i = t.index
      }
    }
    ranges.push([m.index, end])
  }
  return ranges
}

describe('订单详情页：order 未就绪时不得整页崩溃', () => {
  it('售后弹窗仍在页面里（不能被误删）', () => {
    expect(source).toContain('AfterSaleStatusModal')
    expect(source).toContain('showAfterSaleModal')
    expect(source).toContain('showAfterSaleStatusModal')
  })

  it('所有 `order.id` 的模板绑定都落在 v-if="order" 保护范围内', () => {
    const body = templateBody()
    const ranges = guardedRanges()
    // 找到模板里每一处 :order-id="order.id"
    const re = /:order-id="order\.id"/g
    const hits = []
    let m
    while ((m = re.exec(body)) !== null) hits.push(m.index)
    expect(hits.length).toBeGreaterThan(0)

    for (const pos of hits) {
      const covered = ranges.some(([a, b]) => pos > a && pos < b)
      const line = body.slice(0, pos).split('\n').length
      expect(
        covered,
        `OrderDetail.vue 模板第 ${line} 行附近的 :order-id="order.id" 不在 v-if="order" 保护范围内，order 为 null 时会整页白屏`,
      ).toBe(true)
    }
  })
})
