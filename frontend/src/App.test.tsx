// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { bootstrapSession, type AppSession } from './session'
import brandSymbol from './assets/pawspot-symbol-small.svg?no-inline'
import brandLogo from './assets/pawspot-logo.svg?no-inline'

vi.mock('./session', () => ({ bootstrapSession: vi.fn() }))

const session: AppSession = {
  mode: 'preview', api: null,
  profile: { token: '', expires_at: '', user_public_id: '', display_name: 'Тест' },
}

describe('PawSpot branding in the app shell', () => {
  let root: Root
  let container: HTMLDivElement

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    window.history.replaceState(null, '', '/feed')
    vi.mocked(bootstrapSession).mockReset().mockImplementation(() => new Promise(() => undefined))
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
  })

  function fullLogo() {
    const image = container.querySelector<HTMLImageElement>('.center-state img')!
    expect(image.getAttribute('src')).toBe(brandLogo)
    expect(image.alt).toBe('PawSpot — городские истории о котах и собаках')
    expect(container.querySelector('header')).toBeNull()
    expect(container.querySelector('nav')).toBeNull()
  }

  it('shows the full accessible logo while the session is loading', async () => {
    await act(async () => root.render(<App />))
    fullLogo()
    expect(container.textContent).toContain('Открываем городские истории…')
    expect(container.textContent).not.toContain('🐾')
  })

  it('keeps the compact header accessible and preserves navigation after login', async () => {
    vi.mocked(bootstrapSession).mockResolvedValue(session)
    await act(async () => root.render(<App />))
    const header = container.querySelector('header')!
    const symbol = header.querySelector('img')!
    expect(symbol.getAttribute('src')).toBe(brandSymbol)
    expect(symbol.alt).toBe('')
    expect(symbol.getAttribute('aria-hidden')).toBe('true')
    expect(header.querySelector('.brand')?.textContent).toBe('PawSpot')
    expect(header.textContent).toContain('Every city has its characters.')
    expect(header.textContent).not.toContain('🐾')
    const buttons = container.querySelectorAll<HTMLButtonElement>('.bottom-nav button')
    expect(buttons).toHaveLength(4)
    await act(async () => buttons[2].click())
    expect(window.location.pathname).toBe('/collection')
    expect(buttons[2].classList.contains('active')).toBe(true)
    expect(container.querySelector('.center-state')).toBeNull()
  })

  it('keeps the login error and full logo when authentication fails', async () => {
    vi.mocked(bootstrapSession).mockRejectedValue(new Error('Нет доступа к пилоту'))
    await act(async () => root.render(<App />))
    fullLogo()
    expect(container.querySelector('h1')?.textContent).toBe('Не удалось войти')
    expect(container.querySelector('p')?.textContent).toBe('Нет доступа к пилоту')
  })

  it('shows the same full logo and existing message after session expiration', async () => {
    vi.mocked(bootstrapSession).mockResolvedValue(session)
    await act(async () => root.render(<App />))
    await act(async () => window.dispatchEvent(new Event('pawspot-auth-expired')))
    fullLogo()
    expect(container.querySelector('p')?.textContent).toBe('Сессия истекла. Закройте и откройте PawSpot через Telegram заново.')
  })
})
