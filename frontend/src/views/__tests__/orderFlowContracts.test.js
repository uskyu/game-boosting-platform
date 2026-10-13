import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const createSource = readFileSync(new URL('../OrderCreate.vue', import.meta.url), 'utf8')
const detailSource = readFileSync(new URL('../OrderDetail.vue', import.meta.url), 'utf8')
const hallSource = readFileSync(new URL('../OrderHallView.vue', import.meta.url), 'utf8')
const listSource = readFileSync(new URL('../OrderList.vue', import.meta.url), 'utf8')

function compileView(source, id) {
  const { descriptor, errors } = parse(source, { filename: id })
  expect(errors).toEqual([])
  expect(() => compileScript(descriptor, { id })).not.toThrow()
}

describe('order-flow view contracts', () => {
  it('compiles both changed Vue views', () => {
    compileView(createSource, 'OrderCreate.vue')
    compileView(detailSource, 'OrderDetail.vue')
  })

  it('keeps publish disabled across upload and delayed navigation', () => {
    expect(createSource).toMatch(/:disabled="isSubmitting\s*\|\|\s*!canPublish\s*\|\|\s*createdOrderUploadFailed"/)
    expect(createSource).toMatch(/const isSubmitting = computed\(\(\) => submitting\.value \|\| ordersStore\.loading\)/)
    expect(createSource).toMatch(/submitStage\.value = `图片上传中 \$\{index \+ 1\}\/\$\{selected\.length\}`/)
    expect(createSource).toMatch(/await new Promise\(\(resolve\) => window\.setTimeout\(resolve, 900\)\)\s*await router\.push\(/)
    expect(createSource).toMatch(/finally\s*\{\s*submitting\.value = false/)
  })

  it('does not upload after a failed create and keeps file uploads sequential', () => {
    const createIndex = createSource.indexOf('const result = await measureOrderFlowStage(')
    const createFailureIndex = createSource.indexOf('if (!result.success)', createIndex)
    const uploadIndex = createSource.indexOf("'attachment_uploads'", createIndex)
    expect(createIndex).toBeGreaterThan(-1)
    expect(createFailureIndex).toBeGreaterThan(createIndex)
    expect(uploadIndex).toBeGreaterThan(createFailureIndex)
    expect(createSource.slice(createFailureIndex, uploadIndex)).toMatch(/return/)
    expect(createSource).toMatch(/for \(let index = 0; index < selected\.length; index \+= 1\)[\s\S]*?await api\.post\(/)
    expect(createSource).not.toMatch(/Promise\.all\([^)]*uploadAttachments/)
    expect(createSource).toMatch(/createdOrderUploadFailed\.value = true[\s\S]*?请勿重新发布同一订单/)
    expect(createSource).toMatch(/v-if="createdOrderUploadFailed"[\s\S]{0,300}查看已创建订单/)
    expect(createSource).toMatch(/:disabled="isSubmitting \|\| !canPublish \|\| createdOrderUploadFailed"/)
  })

  it('keeps cached hall and published orders visible while they refresh', () => {
    expect(hallSource).toMatch(/v-if="loading && isAuthenticated && !hasHallOrders"/)
    expect(listSource).toMatch(/v-if="loading && !hasPublishedOrders"/)
    expect(hallSource).toMatch(/:aria-busy="loading"/)
    expect(listSource).toMatch(/:aria-busy="loading"/)
    expect(hallSource).toMatch(/refreshOrdersContext\('hall'/)
    expect(listSource).toMatch(/refreshOrdersContext\('minePublished'/)
  })

  it('silently hydrates order details after successful accept and always clears action loading', () => {
    const handlerStart = detailSource.indexOf('async function handleConfirmClaim()')
    const handlerEnd = detailSource.indexOf('\nfunction openDeliverModal()', handlerStart)
    const handler = detailSource.slice(handlerStart, handlerEnd)
    expect(handler).toMatch(/if \(result\.success\)[\s\S]*?ordersStore\.fetchOrder\(orderId, \{ silent: true \}\)/)
    expect(handler).toMatch(/await loadClaims\(\)/)
    expect(handler).toMatch(/let acceptSucceeded = false[\s\S]*?if \(acceptSucceeded\)[\s\S]*?已接手订单，但订单信息刷新失败/)
    expect(handler).toMatch(/catch \{[\s\S]*?errorMessage\.value = '接单失败，请稍后重试'/)
    expect(handler).toMatch(/finally\s*\{\s*actionLoading\.value = false/)
  })
})
