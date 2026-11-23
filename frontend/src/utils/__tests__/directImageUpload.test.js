import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const util = readFileSync(new URL('../directImageUpload.js', import.meta.url), 'utf8')
const createSource = readFileSync(new URL('../../views/OrderCreate.vue', import.meta.url), 'utf8')
const storeSource = readFileSync(new URL('../../stores/orders.js', import.meta.url), 'utf8')

describe('图片直传 COS：绕过服务器中转', () => {
  it('直传走 presign -> PUT -> commit 三步，且 PUT 直接发往 COS', () => {
    expect(util).toContain("api.post('/uploads/presign'")
    expect(util).toContain("api.post('/uploads/commit'")
    expect(util).toMatch(/xhr\.open\('PUT', uploadUrl/)
    // PUT 必须打在预签名 URL 上，不能回落到自家 API
    expect(util).toMatch(/putToCos\(presign\.upload_url/)
  })

  it('本地存储或直传失败时返回 null，调用方回退 multipart', () => {
    expect(util).toMatch(/409|return null/)
    expect(createSource).toMatch(/const direct = await tryDirectImageUpload\(file,[\s\S]{0,200}order_attachment/)
    expect(createSource).toMatch(/if \(direct\) continue[\s\S]{0,200}multipart/)
    expect(storeSource).toMatch(/tryDirectImageUpload\(file,[\s\S]{0,120}delivery_attachment/)
    expect(storeSource).toMatch(/if \(!saved\) \{[\s\S]{0,200}deliver-attachments/)
  })

  it('大图在浏览器本地先压缩，保留 2MB 阈值与 2000px 上限', () => {
    expect(util).toContain('COMPRESS_THRESHOLD_BYTES = 2 * 1024 * 1024')
    expect(util).toContain('MAX_EDGE = 2000')
    expect(util).toContain('JPEG_QUALITY = 0.82')
    expect(util).toMatch(/createImageBitmap/)
  })

  it('只接受 png/jpeg/webp 且不超过 10MB', () => {
    expect(util).toContain("'image/png'")
    expect(util).toContain("'image/jpeg'")
    expect(util).toContain("'image/webp'")
    expect(util).toContain('MAX_UPLOAD_BYTES = 10 * 1024 * 1024')
  })
})
