import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { createRenderer, nextTick, reactive, ssrContextKey } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 组件会 import 签名工具（内部又依赖 axios）；这里整体替换掉，只验证组件行为：
// 渲染 <img>、loading="lazy"、失败后仅换一次 src（不进入重试循环）。
const helpers = vi.hoisted(() => ({
  resolveImageUrl: vi.fn((url) => url),
  handleImageError: vi.fn(),
  isSignedCosUrl: vi.fn(() => true),
  hasSignedUrlCacheEntry: vi.fn(() => false),
}))

vi.mock('@/utils/signedImage', () => ({
  resolveImageUrl: helpers.resolveImageUrl,
  handleImageError: helpers.handleImageError,
  isSignedCosUrl: helpers.isSignedCosUrl,
  hasSignedUrlCacheEntry: helpers.hasSignedUrlCacheEntry,
}))

import SignedImageSfc from '../SignedImage.vue'

// 本仓库没有安装 @vue/test-utils，组件测试沿用现有写法：
// 编译 SFC 的 template 成 render 函数，再用 createRenderer 挂到自建 host。
// SFC 的 <script setup> 由 vite 插件编译成 setup 函数，这里直接复用。
const sfcSource = readFileSync(new URL('../SignedImage.vue', import.meta.url), 'utf8')
const descriptor = parse(sfcSource, { filename: 'SignedImage.vue' }).descriptor
const setupBindings = compileScript(descriptor, { id: 'signed-image-test' }).bindings
const compiled = compileTemplate({
  source: descriptor.template.content,
  filename: 'SignedImage.vue',
  id: 'signed-image-test',
  compilerOptions: { bindingMetadata: setupBindings },
})
const vueRequire = createRequire(import.meta.url)('vue')
const renderFactoryCode = compiled.code
  .replace(/^import \{ ([^}]+) \} from "vue"\n/m, (_match, imports) => {
    const mappings = imports.split(', ').map((part) => {
      const [name, alias] = part.split(' as ')
      return alias ? `${name}: ${alias}` : name
    })
    return `const { ${mappings.join(', ')} } = Vue\n`
  })
  .replace('export function render', 'function render')
const render = new Function('Vue', `${renderFactoryCode}; return render`)(vueRequire)

const SignedImage = { ...SignedImageSfc, render }

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

function findImage(root) {
  return descendants(root).find((node) => node.type === 'img')
}

function mountSignedImage(initialProps = {}) {
  const { renderer } = createTestRenderer()
  const root = createHostNode('root')
  const props = reactive({ rawUrl: '/uploads/orders/pic.jpg', ...initialProps })
  // vitest 的 SSR 转换会给 SFC 的 setup 注入 useSSRContext()，因此必须 provide
  // 一个 SSR 上下文（与 OrderDeliverModal.test.js 相同的做法）。
  const app = renderer.createApp(SignedImage, props).provide(ssrContextKey, {})
  app.mount(root)

  return { root, update: () => app._instance.update(), unmount: () => app.unmount() }
}

// 触发图片加载失败：模板里的 @error 会被 Vue 渲染成宿主事件监听器，
// 由 createRenderer 的 addEventListener 记录到 props['native:error']。
function emitImageError(root) {
  const img = findImage(root)
  expect(img, '应存在 <img> 节点').toBeTruthy()
  const handler = img.props['native:error'] || img.props.onError
  expect(typeof handler, '应注册 error 监听').toBe('function')
  return handler()
}

const RAW = '/uploads/orders/pic.jpg'
const SIGNED = 'https://game-1314124236.cos.ap-chongqing.myqcloud.com/orders/pic.jpg?q-signature=sig'

beforeEach(() => {
  helpers.resolveImageUrl.mockReset()
  helpers.resolveImageUrl.mockImplementation((url) => url)
  helpers.handleImageError.mockReset()
  helpers.handleImageError.mockResolvedValue(null)
  helpers.isSignedCosUrl.mockReset()
  helpers.isSignedCosUrl.mockReturnValue(true)
  helpers.hasSignedUrlCacheEntry.mockReset()
  helpers.hasSignedUrlCacheEntry.mockReturnValue(false)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SignedImage 组件', () => {
  it('渲染一张 <img>，src 由 resolveImageUrl 解析，并保持 loading="lazy"', () => {
    const { root, unmount } = mountSignedImage({ rawUrl: RAW })
    const img = findImage(root)

    expect(img).toBeTruthy()
    expect(img.type).toBe('img')
    expect(helpers.resolveImageUrl).toHaveBeenCalledWith(RAW)
    expect(img.props.src).toBe(RAW)
    expect(img.props.loading).toBe('lazy')
    unmount()
  })

  it('把外部传入的 class / alt 透传到 <img>（布局与语义不变）', () => {
    const { root, unmount } = mountSignedImage({
      rawUrl: RAW,
      alt: '订单附件',
      class: 'h-24 w-full object-cover',
    })
    const img = findImage(root)
    expect(img.props.alt).toBe('订单附件')
    expect(img.props.class).toBe('h-24 w-full object-cover')
    unmount()
  })

  it('命中缓存时直接使用 cached 的签名 URL', () => {
    helpers.resolveImageUrl.mockReturnValue(SIGNED)
    const { root, unmount } = mountSignedImage({ rawUrl: RAW })
    expect(findImage(root).props.src).toBe(SIGNED)
    unmount()
  })

  it('加载失败时调用一次重签并替换 src；只有一次重试，不进入循环', async () => {
    helpers.handleImageError.mockResolvedValue(SIGNED)
    const { root, unmount } = mountSignedImage({ rawUrl: SIGNED })
    // 第一次 error：触发重签
    await emitImageError(root)
    await nextTick()
    expect(helpers.handleImageError).toHaveBeenCalledTimes(1)
    // 用组件当前的 src（签名 URL）作为重签入参
    expect(helpers.handleImageError).toHaveBeenCalledWith(SIGNED)

    expect(findImage(root).props.src).toBe(SIGNED)

    // 第二次 error：已重试过，绝不再发请求
    await emitImageError(root)
    await nextTick()
    expect(helpers.handleImageError).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('重签失败（返回 null）时保持原 src，且不再重试', async () => {
    helpers.handleImageError.mockResolvedValue(null)
    const { root, unmount } = mountSignedImage({ rawUrl: SIGNED })
    await emitImageError(root)
    await nextTick()
    expect(findImage(root).props.src).toBe(SIGNED)
    expect(helpers.handleImageError).toHaveBeenCalledTimes(1)

    await emitImageError(root)
    await nextTick()
    expect(helpers.handleImageError).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('非 COS 签名地址（如 LOCAL 的 /uploads 路径）不触发重签请求', async () => {
    helpers.isSignedCosUrl.mockReturnValue(false)
    const { root, unmount } = mountSignedImage({ rawUrl: RAW })

    await emitImageError(root)
    await nextTick()
    expect(helpers.handleImageError).not.toHaveBeenCalled()
    unmount()
  })

  it('空 rawUrl 时不渲染 <img>', () => {
    const { root, unmount } = mountSignedImage({ rawUrl: '' })
    expect(findImage(root)).toBeFalsy()
    unmount()
  })
})
