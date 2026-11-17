import { readFileSync } from 'node:fs'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const createSource = readFileSync(new URL('../OrderCreate.vue', import.meta.url), 'utf8')
const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')
const listSource = readFileSync(new URL('../OrderList.vue', import.meta.url), 'utf8')
const detailSource = readFileSync(new URL('../OrderDetail.vue', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
  const template = compileTemplate({
    source: descriptor.template.content,
    filename: id,
    id,
  })
  expect(template.errors).toEqual([])
}

function slice(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing start marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('指定接单：发单页 / 大厅 / 我的派单 / 详情页契约', () => {
  it('compiles all changed views', () => {
    compileView(createSource, 'OrderCreate.vue')
    compileView(hallSource, 'OrderHallView.vue')
    compileView(listSource, 'OrderList.vue')
    compileView(detailSource, 'OrderDetail.vue')
  })

  describe('OrderCreate', () => {
    it('提供公开/指定两种接单方式，默认公开并接入 DesignationPicker', () => {
      expect(createSource).toContain("import DesignationPicker from '@/components/order/DesignationPicker.vue'")
      expect(createSource).toMatch(/const claimMode = ref\('public'\)/)
      expect(createSource).toContain('公开接单')
      expect(createSource).toContain('指定接单')
      expect(createSource).toMatch(/<DesignationPicker v-model="designatedUser" \/>/)
    })

    it('指定模式下 max_claims 强制为 1 且输入框禁用', () => {
      expect(createSource).toMatch(/function setClaimMode\(mode\)[\s\S]{0,260}formData\.value\.max_claims = 1/)
      expect(createSource).toMatch(/watch\(\(\) => formData\.value\.max_claims,[\s\S]{0,220}formData\.value\.max_claims = 1/)
      expect(createSource).toMatch(/id="create-max-claims"[\s\S]{0,400}:disabled="claimMode === 'designated'"/)
      expect(createSource).toContain('指定单仅 1 人接单，其他打手看不到也接不了')
    })

    it('canPublish 在指定模式下要求已选人，提交时兜底提示', () => {
      const canPublish = slice(createSource, 'const canPublish = computed', 'function handleGameChange')
      expect(canPublish).toMatch(/claimMode\.value !== 'designated' \|\| Boolean\(designatedUser\.value\)/)
      expect(createSource).toMatch(/if \(claimMode\.value === 'designated' && !designatedUser\.value\) \{\s*errorMessage\.value = '请选择指定打手'/)
    })

    it('payload 带 designated_booster_id，指定模式取指定人 id，公开模式传 null', () => {
      expect(createSource).toMatch(/designated_booster_id: claimMode\.value === 'designated'[\s\S]{0,240}: null/)
      expect(createSource).toMatch(/maxClaims = claimMode\.value === 'designated'\s*\? 1/)
      // 模板不保存指定人，应用模板后指定模式仍需锁回 1 人
      expect(createSource).toMatch(/function applyTemplate\([\s\S]{0,1400}claimMode\.value === 'designated'[\s\S]{0,100}formData\.value\.max_claims = 1/)
    })

    it('重建/续单预填：原单指定人优先，续单兜底用原打手，重建不擅自指定', () => {
      expect(createSource).toMatch(/const designatedPrefill = order\.designated_booster\s*\|\|\s*\(mode === 'repeat' \? order\.booster : null\)/)
      expect(createSource).toMatch(/if \(designatedPrefill\?\.id != null\) \{\s*claimMode\.value = 'designated'/)
      expect(createSource).toMatch(/designatedBoosterName|designatedPrefill\.username/)
    })
  })

  describe('OrderHallView', () => {
    it('指定单显示徽标：目标本人「指定给我」，其余视角「指定单」', () => {
      expect(hallSource).toContain('designated_booster_id')
      expect(hallSource).toContain("'指定给我'")
      expect(hallSource).toContain("'指定单'")
      expect(hallSource).toMatch(/getDesignationBadgeLabel\(order\)[\s\S]{0,200}!bg-primary-soft/)
    })

    it('今日已接单给指定单加标识，样式与续单徽标一致', () => {
      const recent = slice(hallSource, 'v-for="claim in visibleRecentClaims"', '</article>')
      expect(recent).toContain('claim.order.is_designated')
      expect(recent).toMatch(/tag shrink-0 !bg-primary-soft !text-primary">指定单/)
    })
  })

  describe('OrderList', () => {
    it('我的派单卡片显示「指定：@昵称」，昵称缺失回退用户编号', () => {
      expect(listSource).toMatch(/function getDesignationMeta\(order\)/)
      expect(listSource).toContain('order.designated_booster?.username')
      expect(listSource).toContain('`指定：@${username}`')
      expect(listSource).toContain('`指定：用户 #${order.designated_booster_id}`')
      expect(listSource).toMatch(/getDesignationMeta\(order\)[\s\S]{0,120}!bg-primary-soft/)
      // 列表不渲染任何账号字段
      expect(listSource).not.toMatch(/candidate\.email|designated_booster\?\.email/)
    })
  })

  describe('OrderDetail', () => {
    it('关键信息区展示接单方式，并说明仅指定用户可见可接', () => {
      expect(detailSource).toContain('接单方式')
      expect(detailSource).toContain('const designationLabel = computed')
      expect(detailSource).toContain("return '指定给你'")
      expect(detailSource).toContain('`指定给 @${designatedBoosterName.value}`')
      expect(detailSource).toContain('仅该指定用户可见和可接')
      expect(detailSource).toContain('公开接单')
      expect(detailSource).not.toMatch(/designated_booster\?\.email|designated_booster\.email/)
    })

    it('保留桌面悬浮聊天入口与订单附件相关代码', () => {
      expect(detailSource).toContain('ChatFloatingWindow')
      expect(detailSource).toContain('order-chat-fab')
      expect(detailSource).toContain('delivery_attachments')
    })
  })
})
