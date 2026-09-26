import { describe, expect, it, vi } from 'vitest'

import {
  NETWORK_RETRY_DELAYS_MS,
  getNextRetryDelayMs,
  isIdempotentMethod,
  isNetworkLevelError,
  shouldRetryRequest,
} from '../apiRetry'

function networkError(method = 'get') {
  return { message: 'timeout of 10000ms exceeded', config: { method } }
}

function serverError(method = 'get', status = 500) {
  return {
    message: `Request failed with status code ${status}`,
    response: { status, data: {} },
    config: { method },
  }
}

describe('apiRetry network-level retry policy', () => {
  it('treats timeout / reset / DNS failures as network-level', () => {
    expect(isNetworkLevelError(networkError())).toBe(true)
    expect(isNetworkLevelError({ message: 'Network Error', config: {} })).toBe(true)
    // 服务端应答过（哪怕 5xx）不属于连接黑洞，语义归调用方
    expect(isNetworkLevelError(serverError())).toBe(false)
    expect(isNetworkLevelError(null)).toBe(false)
  })

  it('only retries idempotent methods (GET 缺省)', () => {
    expect(isIdempotentMethod('get')).toBe(true)
    expect(isIdempotentMethod('GET')).toBe(true)
    expect(isIdempotentMethod(undefined)).toBe(true)
    expect(isIdempotentMethod('post')).toBe(false)
    expect(isIdempotentMethod('put')).toBe(false)
    expect(isIdempotentMethod('patch')).toBe(false)
    expect(isIdempotentMethod('delete')).toBe(false)
  })

  it('retries a GET exactly once, then stops', () => {
    expect(shouldRetryRequest(networkError('get'), 0)).toBe(true)
    expect(shouldRetryRequest(networkError('get'), 1)).toBe(false)
  })

  it('never retries write requests even on network errors', () => {
    expect(shouldRetryRequest(networkError('post'), 0)).toBe(false)
    expect(shouldRetryRequest(networkError('put'), 0)).toBe(false)
  })

  it('does not retry when the server answered', () => {
    expect(shouldRetryRequest(serverError('get'), 0)).toBe(false)
  })

  it('requires a request config to retry', () => {
    expect(shouldRetryRequest({ message: 'Network Error' }, 0)).toBe(false)
  })

  it('exposes the backoff schedule', () => {
    expect(getNextRetryDelayMs(0)).toBe(800)
    expect(getNextRetryDelayMs(1)).toBeNull()
    expect(NETWORK_RETRY_DELAYS_MS).toHaveLength(1)
  })
})
