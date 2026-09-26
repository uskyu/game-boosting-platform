// 跨境链路（中国大陆 → 境外机房）偶发"单条 TCP 连接黑洞"：请求已发出、
// 响应长时间不回，axios 超时后浏览器（尤其是旧内核）往往仍复用同一条坏
// 连接，用户手动刷新也照样挂——直到连接自行死去才恢复。
// 对策：对幂等的 GET 在"网络层失败"（根本没收到任何 HTTP 响应）时自动换
// 连接重试，用户无感；写请求（POST/PUT/DELETE）绝不自动重试，避免重复
// 提交。策略抽成纯函数便于单测，接线在 utils/api.js 响应拦截器里。

// 第 N 次重试前的等待（毫秒）。只重试一次：黑洞口持续期间多次重试只是
// 继续敲一条死连接，还会把轮询流量放大； flap 型抖动一次重试即可救回。
export const NETWORK_RETRY_DELAYS_MS = [800]

const IDEMPOTENT_METHODS = new Set(['get', 'head', 'options'])

export function isIdempotentMethod(method) {
  return IDEMPOTENT_METHODS.has(String(method || 'get').toLowerCase())
}

// error.response 为空 = 请求没拿到任何响应（超时 ECONNABORTED、连接被
// 重置、DNS 失败）。有 response（哪怕是 5xx）说明服务端应答过，语义上
// 不属于"连接黑洞"，交由调用方决定，不自动重试。
export function isNetworkLevelError(error) {
  return Boolean(error) && !error.response
}

// config 上挂 _netRetryCount 记录已重试次数，跨拦截器递归保持状态。
export function shouldRetryRequest(error, retryCount) {
  const config = error?.config
  if (!config) return false
  if (!isNetworkLevelError(error)) return false
  if (!isIdempotentMethod(config.method)) return false
  return retryCount < NETWORK_RETRY_DELAYS_MS.length
}

export function getNextRetryDelayMs(retryCount) {
  return NETWORK_RETRY_DELAYS_MS[retryCount] ?? null
}
