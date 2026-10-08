import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, h, nextTick, reactive, ssrContextKey } from 'vue'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

const storeMocks = vi.hoisted(() => ({
  uploadDeliverAttachment: vi.fn(),
  deliverOrder: vi.fn(),
}))

vi.mock('@/stores/orders', () => ({
  useOrdersStore: () => storeMocks,
}))

import OrderDeliverModalSfc from '../OrderDeliverModal.vue'

const source = readFileSync(new URL('../OrderDeliverModal.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
const setupBindings = compileScript(descriptor, { id: 'order-deliver-test' }).bindings
const renderCode = compileTemplate({
  source: descriptor.template.content,
  filename: 'OrderDeliverModal.vue',
  id: 'order-deliver-test',
  compilerOptions: { bindingMetadata: setupBindings },
}).code
const vueRequire = createRequire(import.meta.url)('vue')
const renderFactoryCode = renderCode
  .replace(/^import \{ ([^}]+) \} from "vue"\n/m, (_match, imports) => {
    const mappings = imports.split(', ').map((part) => {
      const [name, alias] = part.split(' as ')
      return alias ? `${name}: ${alias}` : name
    })
    return `const { ${mappings.join(', ')} } = Vue\n`
  })
  .replace('export function render', 'function render')
const render = new Function('Vue', `${renderFactoryCode}; return render`)(vueRequire)
const OrderDeliverModal = { ...OrderDeliverModalSfc, render }

function createHostNode(type, text = '') {
  return {
    type,
    text,
    props: {},
    children: [],
    parent: null,
    addEventListener(name, handler) { this.props[`native:${name}`] = handler },
    removeEventListener(name) { delete this.props[`native:${name}`] },
  }
}

function createTestRenderer() {
  const body = createHostNode('body')
  const host = {
    createElement: (type) => createHostNode(type),
    createText: (text) => createHostNode('#text', text),
    createComment: (text) => createHostNode('#comment', text),
    insert(node, parent, anchor = null) {
      if (node.parent) {
        const oldIndex = node.parent.children.indexOf(node)
        if (oldIndex >= 0) node.parent.children.splice(oldIndex, 1)
      }
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(node)
      else parent.children.splice(index, 0, node)
      node.parent = parent
    },
    remove(node) {
      if (!node?.parent) return
      const index = node.parent.children.indexOf(node)
      if (index >= 0) node.parent.children.splice(index, 1)
      node.parent = null
    },
    setText(node, text) { node.text = text },
    setElementText(node, text) {
      node.children = []
      node.text = text
    },
    parentNode: (node) => node.parent,
    nextSibling(node) {
      if (!node.parent) return null
      return node.parent.children[node.parent.children.indexOf(node) + 1] || null
    },
    patchProp(node, key, _previous, value) { node.props[key] = value },
    querySelector(selector) { return selector === 'body' ? body : null },
  }
  return { renderer: createRenderer(host), body }
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)]
}

function textContent(node) {
  return `${node.text || ''}${node.children.map(textContent).join('')}`
}

function findNode(root, predicate) {
  return descendants(root).find(predicate)
}

function createFile(name) {
  return { name, size: 100, type: 'image/png' }
}

function mountModal(initialProps = {}) {
  const { renderer, body } = createTestRenderer()
  const root = createHostNode('root')
  const emitted = []
  const props = reactive({
    modelValue: true,
    orderId: 23,
    attachedCount: 0,
    ...initialProps,
    'onUpdate:modelValue': (value) => emitted.push(['update:modelValue', value]),
    onDeleteExisting: (...args) => emitted.push(['delete-existing', ...args]),
  })
  const app = renderer.createApp(OrderDeliverModal, props).provide(ssrContextKey, {})
  app.mount(root)

  const update = (patch) => {
    Object.assign(props, patch)
    app._instance.render = () => h(OrderDeliverModal, { ...props })
    app._instance.update()
  }

  return { body, emitted, update, unmount: () => app.unmount() }
}

function fileInput(body) {
  return findNode(body, (node) => node.type === 'input' && node.props.type === 'file')
}

function buttonByText(body, label) {
  return descendants(body).find((node) => node.type === 'button' && textContent(node).includes(label))
}

async function pickFiles(input, files) {
  input.props.onChange({ target: { files, value: 'selected' } })
  await nextTick()
}

beforeEach(() => {
  storeMocks.uploadDeliverAttachment.mockReset()
  storeMocks.deliverOrder.mockReset()
  storeMocks.deliverOrder.mockResolvedValue({ success: false, error: '提交失败' })
  vi.stubGlobal('URL', {
    createObjectURL: vi.fn((file) => `blob:${file.name}`),
    revokeObjectURL: vi.fn(),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('OrderDeliverModal attachment limits', () => {
  it('counts existing attachments and accepts only one of two files when four are already saved', async () => {
    const { body, unmount } = mountModal({ attachedCount: 4 })
    const input = fileInput(body)

    expect(input.props.disabled).toBe(false)
    await pickFiles(input, [createFile('one.png'), createFile('two.png')])

    expect(descendants(body).filter((node) => node.type === 'img' && node.props.src?.startsWith('blob:'))).toHaveLength(1)
    expect(textContent(body)).toContain('当前已有 5 张，最多还能上传 0 张')
    expect(fileInput(body).props.disabled).toBe(true)
    unmount()
  })

  it('keeps a successful upload in the count exactly once when attachedCount catches up', async () => {
    storeMocks.uploadDeliverAttachment.mockResolvedValue({ success: true, data: { url: '/uploads/proof.png' } })
    const { body, update, unmount } = mountModal({ attachedCount: 4 })
    await pickFiles(fileInput(body), [createFile('proof.png')])
    buttonByText(body, '提交结单').props.onClick()
    await vi.waitFor(() => expect(storeMocks.uploadDeliverAttachment).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(storeMocks.deliverOrder).toHaveBeenCalledTimes(1))
    await nextTick()

    expect(textContent(body)).toContain('当前已有 5 张，最多还能上传 0 张')
    update({ attachedCount: 5 })
    await nextTick()
    expect(textContent(body)).toContain('当前已有 5 张，最多还能上传 0 张')
    expect(fileInput(body).props.disabled).toBe(true)
    unmount()
  })

  it('clears queued files and upload errors when closed and reopened', async () => {
    storeMocks.uploadDeliverAttachment.mockResolvedValue({ success: false, error: 'Network error' })
    const { body, update, unmount } = mountModal()
    await pickFiles(fileInput(body), [createFile('retry.png')])
    buttonByText(body, '提交结单').props.onClick()
    await vi.waitFor(() => expect(textContent(body)).toContain('Network error'))
    expect(descendants(body).filter((node) => node.type === 'img' && node.props.src?.startsWith('blob:'))).toHaveLength(1)

    buttonByText(body, '关闭').props.onClick()
    update({ modelValue: false })
    await nextTick()
    expect(findNode(body, (node) => node.type === 'div' && node.props.role === 'dialog')).toBeUndefined()
    update({ modelValue: true })
    await nextTick()

    expect(textContent(body)).not.toContain('Network error')
    expect(descendants(body).filter((node) => node.type === 'img' && node.props.src?.startsWith('blob:'))).toHaveLength(0)
    expect(fileInput(body).props.disabled).toBe(false)
    unmount()
  })

  it('emits the existing attachment index and value when its delete control is clicked', async () => {
    const attachment = { url: '/uploads/saved.png', filename: 'saved.png' }
    const { body, emitted, unmount } = mountModal({ existingAttachments: [attachment], attachedCount: 1 })
    const removeButton = findNode(body, (node) => node.type === 'button' && node.props['aria-label'] === '删除已保存附件 1')

    expect(removeButton).toBeDefined()
    removeButton.props.onClick()
    expect(emitted).toContainEqual(['delete-existing', 0, attachment])
    unmount()
  })
})
