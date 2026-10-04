export interface TelegramWebApp {
  initData: string
  ready(): void
  expand(): void
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp }
  }
}

export function telegramApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp
}
