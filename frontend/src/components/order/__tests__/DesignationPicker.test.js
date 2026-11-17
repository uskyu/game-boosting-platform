import { readFileSync } from 'node:fs'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  new URL('../DesignationPicker.vue', import.meta.url),
  'utf8',
)

function compileView(sourceText, id) {
  const { descriptor, errors } = parse(sourceText, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
  const template = compileTemplate({
    source: descriptor.template.content,
    filename: id,
    id,
  })
  expect(template.errors).toEqual([])
}

describe('DesignationPicker：指定打手选择器契约', () => {
  it('compiles the component (script + template)', () => {
    compileView(source, 'DesignationPicker.vue')
  })

  it('v-model 绑定选中用户，并调用 orders store 的候选 action（不裸调 api）', () => {
    expect(source).toMatch(/defineProps\(\{[\s\S]{0,120}modelValue/)
    expect(source).toContain("emit('update:modelValue'")
    expect(source).toContain("import { useOrdersStore } from '@/stores/orders'")
    expect(source).toContain('ordersStore.fetchDesignationCandidates')
    expect(source).not.toMatch(/from '@\/utils\/api'/)
  })

  it('只展示昵称与保证金，绝不渲染账号/邮箱字段', () => {
    // 候选可能支持按账号搜索，但页面任何位置都不得输出账号/邮箱
    expect(source).not.toMatch(/\bemail\b/i)
    expect(source).not.toMatch(/candidate\.account/)
    expect(source).toContain('candidate.username')
    expect(source).toContain("import { formatPrice } from '@/utils/display'")
    expect(source).toContain('保证金 {{ formatPrice(candidate.deposit_balance) }}')
    expect(source).toMatch(/v-if="hasDeposit\(selectedUser\)"/)
    expect(source).toContain('保证金 {{ formatPrice(selectedUser.deposit_balance) }}')
  })

  it('搜索 300ms 防抖、少于 2 字符不请求，并用请求序号阻止旧响应覆盖', () => {
    expect(source).toMatch(/term\.length < 2/)
    expect(source).toMatch(/window\.setTimeout\([\s\S]{0,140}runSearch\(term\)[\s\S]{0,30}\}, 300\)/)
    expect(source).toMatch(/const requestSeq = \+\+searchSeq/)
    expect(source).toMatch(/if \(requestSeq !== searchSeq\) return/)
    // 输入变化立即作废在飞请求，避免旧关键词结果回填
    expect(source).toMatch(/watch\(searchQuery,[\s\S]{0,260}searchSeq \+= 1/)
  })

  it('候选行包含头像/首字母、已停接标签与不可选状态', () => {
    expect(source).toContain('userInitial(candidate)')
    expect(source).toMatch(/v-if="!candidate\.avatar_url"/)
    expect(source).toMatch(/:src="candidate\.avatar_url"/)
    expect(source).toContain('已停接')
    expect(source).toMatch(/v-if="candidate\.can_accept === false"/)
    expect(source).toMatch(/:disabled="!isAcceptable\(candidate\)"/)
    expect(source).toMatch(/function isAcceptable\(candidate\) \{\s*return candidate\?\.can_accept !== false/)
  })

  it('结果使用内联可滚动面板，不是 absolute 下拉', () => {
    expect(source).toContain('max-h-[280px]')
    expect(source).toContain('overflow-y-auto')
    expect(source).not.toMatch(/\babsolute\b/)
    expect(source).toContain('最近指定')
    expect(source).toContain('搜索结果')
    // loading / 空 / 无结果 / 错误均有中文反馈
    expect(source).toContain('正在搜索…')
    expect(source).toContain('正在加载最近指定…')
    expect(source).toContain('暂无最近指定')
    expect(source).toContain('没有找到匹配的用户')
    expect(source).toContain('最近指定加载失败')
    expect(source).toContain('搜索失败，请稍后重试')
    expect(source).toContain('>重试<')
  })

  it('选中后展示用户卡片，支持更换并恢复搜索', () => {
    expect(source).toMatch(/v-if="selectedUser"[\s\S]{0,1200}更换/)
    expect(source).toMatch(/async function changeDesignatedUser\(\)[\s\S]{0,420}emit\('update:modelValue', null\)/)
    expect(source).toMatch(/async function changeDesignatedUser\(\)[\s\S]{0,520}searchInput\.value\?\.focus\(\)/)
  })

  it('键盘可达：Enter 选第一个可接候选人，Escape 清空并收起结果', () => {
    expect(source).toMatch(/@keydown\.enter\.prevent="handleEnter"/)
    expect(source).toMatch(/@keydown\.escape\.prevent="handleEscape"/)
    expect(source).toMatch(/const candidate = activeCandidates\.value\.find\(\(item\) => isAcceptable\(item\)\)/)
    expect(source).toMatch(/function handleEscape\(\)[\s\S]{0,320}searchQuery\.value = ''/)
    expect(source).toMatch(/placeholder="搜索昵称或账号"/)
  })
})
