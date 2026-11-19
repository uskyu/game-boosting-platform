import { describe, expect, it } from 'vitest'

import { extractModuleEntry, isChunkLoadError } from '../appVersion'

describe('appVersion', () => {
  it('extracts the Vite module entry from index.html', () => {
    const html = '<html><body><div id="app"></div><script type="module" crossorigin src="/assets/index-abc123.js"></script></body></html>'
    expect(extractModuleEntry(html)).toBe('/assets/index-abc123.js')
  })

  it('supports the script tag with attributes in another order', () => {
    const html = '<script src="/assets/index-def456.js" type="module"></script>'
    expect(extractModuleEntry(html)).toBe('/assets/index-def456.js')
  })

  it('returns an empty string when there is no module entry', () => {
    expect(extractModuleEntry('')).toBe('')
    expect(extractModuleEntry('<html><body>no script</body></html>')).toBe('')
  })

  it('detects chunk load failures from browser error messages', () => {
    expect(isChunkLoadError(new Error('Failed to fetch dynamically imported module: https://x/assets/OrderCreate.js'))).toBe(true)
    expect(isChunkLoadError(new Error('Importing a module script failed.'))).toBe(true)
    expect(isChunkLoadError({ message: 'ChunkLoadError: Loading chunk 12 failed' })).toBe(true)
    expect(isChunkLoadError(new Error('Request failed with status code 401'))).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
  })
})
