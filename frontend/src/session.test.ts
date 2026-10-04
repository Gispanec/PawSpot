import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { bootstrapSession } from './session'
import { ApiClient, ApiError } from './api'

describe('Mini App session', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_DEV_PREVIEW', 'false')
    vi.stubGlobal('window', { Telegram: { WebApp: {
      initData: 'signed-raw-init-data', ready: vi.fn(), expand: vi.fn(),
    } } })
  })
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals() })

  it('exchanges raw Telegram initData and keeps bearer session in memory', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      token: 'opaque-test-session', expires_at: '2026-10-04T12:00:00Z',
      user_public_id: 'user-id', display_name: 'Гиви',
    }) })
    vi.stubGlobal('fetch', fetcher)
    const session = await bootstrapSession()
    expect(session.mode).toBe('telegram')
    expect(session.profile.display_name).toBe('Гиви')
    expect(fetcher).toHaveBeenCalledWith('/api/v1/auth/telegram', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ init_data: 'signed-raw-init-data' }),
    }))
    expect(window.Telegram?.WebApp?.ready).toHaveBeenCalled()
    expect(window.Telegram?.WebApp?.expand).toHaveBeenCalled()
  })

  it('does not grant access without Telegram initData', async () => {
    vi.stubGlobal('window', {})
    await expect(bootstrapSession()).rejects.toThrow('Откройте PawSpot через Telegram')
  })

  it('reports server authentication rejection', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))
    await expect(bootstrapSession()).rejects.toMatchObject({ status: 403 })
  })

  it('sends opaque bearer token only to authenticated API requests', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) })
    vi.stubGlobal('fetch', fetcher)
    await new ApiClient('opaque-token').request('/feed')
    expect(fetcher).toHaveBeenCalledWith('/api/v1/feed', expect.any(Object))
    expect((fetcher.mock.calls[0][1].headers as Headers).get('Authorization')).toBe('Bearer opaque-token')
    expect(new ApiError(401, 'x').status).toBe(401)
  })
})
