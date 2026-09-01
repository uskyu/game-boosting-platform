import { describe, it, expect } from 'vitest'
import {
  getTimeGreeting,
  getServiceTypeLabel,
  getServiceTypeCTA,
  getPublishButtonLabel,
  getOrderStatusCopy,
} from '../humanCopy.js'

describe('getTimeGreeting', () => {
  it('早上 6–10 点返回早上问候', () => {
    expect(getTimeGreeting(8)).toBe('早上好，今天先冲一把？')
  })

  it('下午 11–17 点返回下午问候', () => {
    expect(getTimeGreeting(14)).toBe('下午了，找个代练上分？')
  })

  it('晚上 18–23 点返回晚上问候', () => {
    expect(getTimeGreeting(20)).toBe('今晚要上分还是陪玩？')
  })

  it('凌晨 0–5 点返回凌晨问候', () => {
    expect(getTimeGreeting(2)).toBe('还没睡？来一把放松一下')
  })
})

describe('getServiceTypeLabel', () => {
  it('代练 返回 帮你上号代打', () => {
    expect(getServiceTypeLabel('代练')).toBe('帮你上号代打')
  })

  it('陪玩 返回 组队一起玩', () => {
    expect(getServiceTypeLabel('陪玩')).toBe('组队一起玩')
  })

  it('教学 返回 教学陪玩', () => {
    expect(getServiceTypeLabel('教学')).toBe('教学陪玩')
  })

  it('未知类型原样返回', () => {
    expect(getServiceTypeLabel('其他')).toBe('其他')
  })
})

describe('getServiceTypeCTA', () => {
  it('代练 返回 找代练上分', () => {
    expect(getServiceTypeCTA('代练')).toBe('找代练上分')
  })

  it('陪玩 返回 找陪玩搭子', () => {
    expect(getServiceTypeCTA('陪玩')).toBe('找陪玩搭子')
  })

  it('教学 返回 找教学陪玩', () => {
    expect(getServiceTypeCTA('教学')).toBe('找教学陪玩')
  })

  it('未知类型返回默认 CTA', () => {
    expect(getServiceTypeCTA('未知')).toBe('立即下单')
  })
})

describe('getPublishButtonLabel', () => {
  it('代练 返回 发布代练需求', () => {
    expect(getPublishButtonLabel('代练')).toBe('发布代练需求')
  })

  it('陪玩 返回 发布陪玩需求', () => {
    expect(getPublishButtonLabel('陪玩')).toBe('发布陪玩需求')
  })

  it('教学 返回 发布教学需求', () => {
    expect(getPublishButtonLabel('教学')).toBe('发布教学需求')
  })

  it('空类型返回默认', () => {
    expect(getPublishButtonLabel('')).toBe('发布需求')
  })
})

describe('getOrderStatusCopy', () => {
  it('PENDING + 代练', () => {
    const { label, subtitle } = getOrderStatusCopy('PENDING', '代练')
    expect(label).toBe('等待代练接单')
    expect(subtitle).toBe('需求已发出，代练们正在看')
  })

  it('PENDING + 陪玩', () => {
    const { label, subtitle } = getOrderStatusCopy('PENDING', '陪玩')
    expect(label).toBe('等待陪玩接单')
    expect(subtitle).toBe('需求已发出，陪玩们正在看')
  })

  it('LOCKED + 代练', () => {
    const { label, subtitle } = getOrderStatusCopy('LOCKED', '代练')
    expect(label).toBe('代练上号中')
    expect(subtitle).toBe('代练正在使用你的账号上分')
  })

  it('LOCKED + 陪玩', () => {
    const { label, subtitle } = getOrderStatusCopy('LOCKED', '陪玩')
    expect(label).toBe('陪玩进行中')
    expect(subtitle).toBe('陪玩已就位，一起开黑吧')
  })

  it('COMPLETED + 代练', () => {
    const { label } = getOrderStatusCopy('COMPLETED', '代练')
    expect(label).toBe('代练完成了！')
  })

  it('COMPLETED + 陪玩', () => {
    const { label } = getOrderStatusCopy('COMPLETED', '陪玩')
    expect(label).toBe('这局打完了！')
  })

  it('CANCELLED 不区分类型', () => {
    const { label } = getOrderStatusCopy('CANCELLED', '代练')
    expect(label).toBe('订单已取消')
  })

  it('LOCKED + 教学（归属陪玩语境）', () => {
    const { label } = getOrderStatusCopy('LOCKED', '教学')
    expect(label).toBe('陪玩进行中')
  })

  it('DISPUTED 状态', () => {
    const { label, subtitle } = getOrderStatusCopy('DISPUTED', '代练')
    expect(label).toBe('订单争议中')
    expect(subtitle).toBe('平台正在介入处理')
  })

  it('COMPLETED 副标题', () => {
    const { subtitle } = getOrderStatusCopy('COMPLETED', '代练')
    expect(subtitle).toBe('记得说说这次体验')
  })

  it('未知状态返回原始值', () => {
    const { label, subtitle } = getOrderStatusCopy('UNKNOWN_STATUS', '代练')
    expect(label).toBe('UNKNOWN_STATUS')
    expect(subtitle).toBe('')
  })
})
