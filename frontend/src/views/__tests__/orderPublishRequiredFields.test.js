import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const createSource = readFileSync(
  new URL('../OrderCreate.vue', import.meta.url),
  'utf8'
)
const adminSource = readFileSync(new URL('../AdminView.vue', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('发单表单：标题必填 / 需求描述可选（老板 2026-10-19）', () => {
  it('compiles the publish page', () => {
    compileView(createSource, 'OrderCreate.vue')
  })

  it('marks the title as required and the description as optional', () => {
    // 标签文案必须反过来：标题带 *，描述标注「可选」
    const titleLabel = createSource.slice(
      createSource.indexOf('for="create-title"'),
      createSource.indexOf('</label>', createSource.indexOf('for="create-title"'))
    )
    expect(titleLabel).toMatch(/订单标题/)
    expect(titleLabel).not.toMatch(/可选/)

    const descLabel = createSource.slice(
      createSource.indexOf('for="create-description"'),
      createSource.indexOf('</label>', createSource.indexOf('for="create-description"'))
    )
    expect(descLabel).toMatch(/需求描述/)
    expect(descLabel).toMatch(/可选/)
    // 描述不再带必填星号
    expect(descLabel).not.toMatch(/\*/)
  })

  it('gates publishing on the title, not on the description', () => {
    const canPublish = createSource.slice(
      createSource.indexOf('const canPublish'),
      createSource.indexOf('function handleGameChange')
    )
    expect(canPublish).toMatch(/formData\.value\.title\.trim\(\)/)
    expect(canPublish).not.toMatch(/formData\.value\.description_raw\.trim\(\)/)
  })

  it('always sends a non-empty title and allows an empty description', () => {
    expect(createSource).toMatch(/if \(!title\)[\s\S]{0,80}请填写订单标题/)
    // 留空描述传 null，后端归一为 NULL
    expect(createSource).toMatch(/description_raw: formData\.value\.description_raw\.trim\(\) \|\| null/)
    // 标题不再允许落到 null
    expect(createSource).not.toMatch(/title: title \|\| null/)
  })

  it('keeps the admin publish dialog consistent with the same rule', () => {
    // 管理员发单没填标题时自动生成「游戏名 派单」，不留无标题订单
    expect(adminSource).toMatch(/title: state\.title\.trim\(\) \|\| `\$\{game\.name\} 派单`/)
  })
})
