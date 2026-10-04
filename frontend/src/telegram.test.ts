import { describe, expect, it } from 'vitest'
import indexHtml from '../index.html?raw'

describe('Telegram Web App bootstrap', () => {
  it('loads the official SDK before the application module', () => {
    const sdk = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
    expect(indexHtml).toContain(sdk)
    expect(indexHtml.indexOf(sdk)).toBeLessThan(indexHtml.indexOf('src="/src/main.tsx"'))
  })
})
