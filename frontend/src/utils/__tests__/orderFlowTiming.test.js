import { afterEach, describe, expect, it, vi } from 'vitest'
import { measureOrderFlowStage } from '../orderFlowTiming.js'

describe('measureOrderFlowStage', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('logs only the fixed event, stage, duration, and sanitized count', async () => {
    vi.spyOn(performance, 'now')
      .mockReturnValueOnce(12.25)
      .mockReturnValueOnce(19.5)
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})

    const result = await measureOrderFlowStage(
      'order_publish',
      'create_api',
      async () => 'created',
      2.8,
    )

    expect(result).toBe('created')
    expect(info).toHaveBeenCalledOnce()
    expect(info).toHaveBeenCalledWith('order_flow_timing', {
      event_name: 'order_publish',
      stage: 'create_api',
      duration_ms: 7.25,
      count: 2,
    })
  })

  it('records elapsed time if the measured operation rejects', async () => {
    vi.spyOn(performance, 'now')
      .mockReturnValueOnce(3)
      .mockReturnValueOnce(8)
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})
    const failure = new Error('request failed')

    await expect(measureOrderFlowStage(
      'order_accept',
      'accept_api',
      async () => { throw failure },
    )).rejects.toBe(failure)

    expect(info).toHaveBeenCalledWith('order_flow_timing', {
      event_name: 'order_accept',
      stage: 'accept_api',
      duration_ms: 5,
      count: 1,
    })
  })

  it('rejects labels outside the fixed allowlist without logging them', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})

    await expect(measureOrderFlowStage(
      'order_publish',
      'customer@example.com',
      async () => {},
    )).rejects.toThrow('Unknown order-flow timing event or stage')

    expect(info).not.toHaveBeenCalled()
  })
})
