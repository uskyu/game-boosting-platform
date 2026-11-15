import { readFileSync } from 'node:fs'
import { compileScript, compileStyle, compileTemplate, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const floatingSource = readFileSync(new URL('../ChatFloatingWindow.vue', import.meta.url), 'utf8')
const panelSource = readFileSync(new URL('../ChatPanel.vue', import.meta.url), 'utf8')
const composerSource = readFileSync(new URL('../ChatComposer.vue', import.meta.url), 'utf8')
const messageListSource = readFileSync(new URL('../ChatMessageList.vue', import.meta.url), 'utf8')

function compileSfc(source, filename) {
  const { descriptor, errors } = parse(source, { filename })
  expect(errors).toEqual([])

  const script = compileScript(descriptor, { id: filename })
  const template = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  })

  expect(template.errors).toEqual([])

  for (const style of descriptor.styles) {
    const result = compileStyle({
      source: style.content,
      filename,
      id: 'data-v-chat-test',
      scoped: style.scoped,
    })
    expect(result.errors).toEqual([])
  }

  return descriptor
}

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker)
  expect(start, `missing start marker: ${startMarker}`).toBeGreaterThan(-1)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(end, `missing end marker: ${endMarker}`).toBeGreaterThan(start)
  return source.slice(start + startMarker.length, end)
}

function templateOf(source) {
  return sliceBetween(source, '<template>', '</template>')
}

function styleOf(source) {
  return sliceBetween(source, '<style scoped>', '</style>')
}

function scriptOf(source) {
  return sliceBetween(source, '<script setup>', '</script>')
}

function blockOf(source, selector) {
  const start = source.indexOf(selector)
  expect(start, `missing block: ${selector}`).toBeGreaterThan(-1)
  const openIndex = source.indexOf('{', start + selector.length)
  let depth = 0

  for (let index = openIndex; index < source.length; index += 1) {
    if (source[index] === '{') {
      depth += 1
    } else if (source[index] === '}') {
      depth -= 1
      if (depth === 0) {
        return source.slice(start, index + 1)
      }
    }
  }

  throw new Error(`unclosed block: ${selector}`)
}

describe('ChatFloatingWindow 悬浮聊天窗', () => {
  it('compiles the floating window and the panel', () => {
    compileSfc(floatingSource, 'ChatFloatingWindow.vue')
    compileSfc(panelSource, 'ChatPanel.vue')
  })

  it('teleports to body and only mounts ChatPanel when open with a conversation id', () => {
    const template = templateOf(floatingSource)

    expect(floatingSource).toMatch(/<Teleport to="body">/)
    expect(template).toMatch(/v-if="open && conversationId"/)
    expect(template).not.toMatch(/v-show=/)

    const panel = sliceBetween(template, '<ChatPanel', '/>')
    expect(panel).toMatch(/:conversation-id="conversationId"/)
    expect(panel).toMatch(/\bcompact\b/)
    expect(panel).toMatch(/\bfloating\b/)
    expect(panel).toMatch(/\bshow-close\b/)
  })

  it('emits close and expand from the floating wrapper', () => {
    const script = scriptOf(floatingSource)
    const template = templateOf(floatingSource)

    expect(script).toMatch(/defineEmits\(\[\s*['"]close['"],\s*['"]expand['"]\s*\]\)/)
    expect(script).toMatch(/emit\('close'\)/)
    expect(template).toMatch(/@close="requestClose"/)
    expect(template).toMatch(/@expand="emit\('expand'\)"/)
  })

  it('closes on Escape but yields to image preview / lightbox overlays', () => {
    const script = scriptOf(floatingSource)

    expect(script).toMatch(/event\.key !== 'Escape'/)
    expect(script).toMatch(/document\.addEventListener\('keydown', onKeydown\)/)
    expect(script).toMatch(/document\.removeEventListener\('keydown', onKeydown\)/)
    expect(script).toMatch(/document\.querySelectorAll\('\.modal-scrim, \.lb-overlay'\)/)
    expect(script).toMatch(/element !== scrimRef\.value/)
    expect(script).toMatch(/if \(hasBlockingOverlay\(\)\) \{\s*return\s*\}/)
  })

  it('renders a click-to-close scrim only below 1280px', () => {
    const template = templateOf(floatingSource)

    expect(template).toMatch(/<div\s+v-if="!isDesktop"\s+ref="scrimRef"\s+class="modal-scrim chat-float-scrim"[\s\S]*?@click\.self="requestClose"/)
    expect(template).toMatch(/role="presentation"/)
    // 点击抽屉内部不能关闭：只有遮罩自身使用 click.self
    expect(template).not.toMatch(/@click="requestClose"/)
  })

  it('uses an accessible non-modal desktop dialog', () => {
    const template = templateOf(floatingSource)
    const script = scriptOf(floatingSource)

    expect(template).toMatch(/role="dialog"/)
    expect(template).toMatch(/:aria-modal="isDesktop \? 'false' : 'true'"/)
    expect(template).toMatch(/:aria-label="title \|\| '聊天窗口'"/)
    expect(template).toMatch(/tabindex="-1"/)
    expect(script).toMatch(/const DESKTOP_QUERY = '\(min-width: 1280px\)'/)
    expect(script).toMatch(/window\.matchMedia\(DESKTOP_QUERY\)/)
  })

  it('focuses the panel close button on open and restores the trigger on close', () => {
    const script = scriptOf(floatingSource)

    expect(script).toMatch(/querySelector\('\[data-chat-close\]'\)/)
    expect(script).toMatch(/nextTick\(/)
    expect(script).toMatch(/focus\(\{ preventScroll: true \}\)/)
    expect(script).toMatch(/previousFocus/)
    expect(script).toMatch(/target\.isConnected/)
  })

  it('keeps the desktop card floating above header/tabbar and below the image viewer', () => {
    const style = styleOf(floatingSource)
    const desktop = blockOf(style, '.chat-float-card--desktop')
    const scrim = blockOf(style, '.chat-float-scrim')
    const card = blockOf(style, '.chat-float-card')

    expect(desktop).toMatch(/position: fixed;/)
    expect(desktop).toMatch(/right: 24px;/)
    expect(desktop).toMatch(/bottom: 24px;/)
    expect(desktop).toMatch(/width: min\(420px, calc\(100vw - 32px\)\);/)
    expect(desktop).toMatch(/height: min\(640px, calc\(100dvh - 120px\)\);/)
    expect(desktop).toMatch(/max-height: calc\(100dvh - 120px\);/)
    expect(desktop).toMatch(/height: 640px;/)
    expect(desktop).toMatch(/border-radius: 22px;/)

    // 老内核没有 dvh 时先有 vh 兜底，再被 dvh 覆盖
    expect(desktop.indexOf('calc(100vh - 120px)')).toBeLessThan(
      desktop.indexOf('calc(100dvh - 120px)'),
    )

    expect(card).toMatch(/z-index: 61;/)
    expect(scrim).toMatch(/z-index: 60;/)
    expect(Number(card.match(/z-index: (\d+)/)[1])).toBeGreaterThan(50)
    expect(Number(card.match(/z-index: (\d+)/)[1])).toBeLessThan(120)
    // 桌面不锁页面滚动
    expect(scriptOf(floatingSource)).not.toMatch(/document\.body\.style\.overflow/)
  })

  it('uses a safe-area bottom sheet below 1280px with keyboard-aware height', () => {
    const style = styleOf(floatingSource)
    const sheet = blockOf(style, '.chat-float-card--sheet')
    const script = scriptOf(floatingSource)

    expect(sheet).toMatch(/position: fixed;/)
    expect(sheet).toMatch(/left: 0;/)
    expect(sheet).toMatch(/bottom: 0;/)
    expect(sheet).toMatch(/height: min\(88dvh, calc\(100dvh - 12px\)\);/)
    expect(sheet).toMatch(/max-height: calc\(100dvh - 12px\);/)
    expect(sheet).toMatch(/padding-bottom: 0;/)
    expect(sheet).toMatch(/padding-bottom: env\(safe-area-inset-bottom\);/)
    expect(sheet.indexOf('padding-bottom: 0;')).toBeLessThan(
      sheet.indexOf('padding-bottom: env(safe-area-inset-bottom);'),
    )
    expect(sheet).toMatch(/border-radius: 22px 22px 0 0;/)

    expect(sheet.indexOf('calc(100vh - 12px)')).toBeLessThan(
      sheet.indexOf('calc(100dvh - 12px)'),
    )

    expect(script).toMatch(/window\.visualViewport\.addEventListener\('resize', syncViewport\)/)
    expect(script).toMatch(/window\.visualViewport\.addEventListener\('scroll', syncViewport\)/)
    expect(script).toMatch(/viewport\.offsetTop/)
    expect(script).toMatch(/window\.innerHeight/)
    expect(script).toMatch(/keyboardInset/)
    expect(script).toMatch(/height: `\$\{availableHeight\}px`/)
  })

  it('does not duplicate the conversation title in the floating chrome', () => {
    const template = templateOf(floatingSource)

    expect(template).not.toMatch(/<h1/)
    expect(template).not.toMatch(/conversationTitle/)
    expect(template).toMatch(/:aria-label="title \|\| '聊天窗口'"/)
  })
})

describe('ChatPanel 悬浮/紧凑模式', () => {
  it('adds compact/showClose/floating props without changing defaults', () => {
    const script = scriptOf(panelSource)

    for (const name of ['compact', 'showClose', 'floating']) {
      const block = blockOf(script, `${name}:`)
      expect(block).toMatch(/type: Boolean/)
      expect(block).toMatch(/default: false/)
    }

    expect(script).toMatch(/defineEmits\(\[\s*['"]close['"],\s*['"]expand['"]\s*\]\)/)
  })

  it('hides search and admin-invite controls in compact mode', () => {
    const template = templateOf(panelSource)

    expect(template.match(/v-if="!compact && searchOpen"/g)).toHaveLength(2)
    expect(template).toMatch(/v-else-if="!compact"/)
    expect(template).toMatch(/v-if="!compact && !hasAdmin && !authStore\.isAdmin"/)
    expect(template).toMatch(/v-else-if="!compact && hasAdmin"/)

    // 旧的无条件头部操作必须全部收进 compact 判断
    expect(template).not.toMatch(/v-if="!hasAdmin && !authStore\.isAdmin"/)
    expect(template).not.toMatch(/v-else-if="hasAdmin"/)
    expect(template).not.toMatch(/v-if="searchOpen"/)
  })

  it('renders close/expand buttons on the header right when enabled', () => {
    const template = templateOf(panelSource)

    expect(template).toMatch(/v-if="showClose"/)
    expect(template).toMatch(/data-chat-close/)
    expect(template).toMatch(/@click="emit\('close'\)"/)
    expect(template).toMatch(/aria-label="关闭聊天窗口"/)
    // 会话加载失败时头部不渲染，悬浮窗仍要有关闭入口
    expect(template).toMatch(/<div v-else class="empty-state !py-20">[\s\S]*?data-chat-close/)

    expect(template).toMatch(/v-if="floating"/)
    expect(template).toMatch(/data-chat-expand/)
    expect(template).toMatch(/@click="emit\('expand'\)"/)
    expect(template).toMatch(/aria-label="展开聊天窗口"/)
  })

  it('adds the floating shell class and removes the 24rem min-height only when floating', () => {
    const template = templateOf(panelSource)
    const style = styleOf(panelSource)

    expect(template).toMatch(/:class="\{ 'chat-shell--floating': floating \}"/)
    expect(blockOf(style, '.chat-shell--floating')).toMatch(/min-height: 0;/)
  })

  it('keeps the header on one line with truncating title and shrink-0 actions', () => {
    const template = templateOf(panelSource)
    const style = styleOf(panelSource)

    expect(template).toMatch(/:class="\{ 'chat-panel-header--compact': compact \}"/)
    expect(blockOf(style, '.chat-panel-header--compact')).toMatch(/flex-wrap: nowrap;/)
    expect(template).toMatch(/<h1 class="truncate text-lg font-semibold text-ink-1">/)
    expect(template).toMatch(/<div class="flex shrink-0 items-center gap-2">/)
    expect(template).toMatch(/<span class="chat-status-pill shrink-0">/)
  })

  it('keeps the default (non-compact) panel capabilities intact', () => {
    const template = templateOf(panelSource)

    expect(template).toMatch(/<section[\s\S]{0,220}class="chat-shell"/)
    expect(template).toMatch(/<ChatSearchBar/)
    expect(template).toMatch(/@click="handleInviteAdmin"/)
    expect(template).toMatch(/<ChatMessageList/)
    expect(template).toMatch(/<ChatComposer/)
  })

  it('supports dragging and pasting chat images', () => {
    compileSfc(composerSource, 'ChatComposer.vue')
    const panelTemplate = templateOf(panelSource)
    const panelScript = scriptOf(panelSource)
    const composerTemplate = templateOf(composerSource)
    const composerScript = scriptOf(composerSource)

    expect(composerTemplate).toMatch(/@paste="handlePaste"/)
    expect(composerTemplate).toMatch(/\bmultiple\b/)
    expect(composerScript).toMatch(/chatImageFilesFromClipboard/)
    expect(composerScript).toMatch(/emit\('send-images', files\)/)
    expect(composerScript).toMatch(/emit\('image-error', error\)/)
    expect(panelTemplate).toMatch(/@send-images="handleSendImages"/)
    expect(panelTemplate).toMatch(/@image-error="handleImageError"/)
    expect(panelTemplate).toMatch(/@drop="handleDrop"/)
    expect(panelTemplate).toMatch(/@dragover="handleDragOver"/)
    expect(panelTemplate).toMatch(/chat-drop-overlay/)
    expect(panelScript).toMatch(/chatImageFilesFromDataTransfer/)
    expect(panelScript).toMatch(/validateChatImageFiles/)
  })

  it('sticks to bottom again when an image message finishes loading', () => {
    const template = templateOf(messageListSource)
    const script = scriptOf(messageListSource)

    expect(template).toMatch(/@load\.capture="handleMediaLoad"/)
    expect(script).toMatch(/followMediaLoad = true/)
    expect(script).toMatch(/async function handleMediaLoad/)
  })

  it('passes compact down to ChatComposer so the narrow input stays readable', () => {
    const panelTemplate = templateOf(panelSource)
    const composerTemplate = templateOf(composerSource)
    const composerScript = scriptOf(composerSource)

    expect(panelTemplate).toMatch(/<ChatComposer[\s\S]*?:compact="compact"/)
    expect(composerScript).toMatch(/compact: \{[\s\S]{0,80}default: false/)
    expect(composerTemplate).toMatch(/v-if="!compact" class="chat-security-notice"/)
    expect(composerTemplate).toMatch(/:placeholder="compact \? '输入消息…'/)
    // 完整聊天页仍保留安全提示和完整 placeholder
    expect(composerTemplate).toMatch(/请勿在聊天中发送账号密码等敏感信息/)
    expect(composerTemplate).toMatch(/输入聊天内容，Enter 发送，Shift \+ Enter 换行/)
  })
})
