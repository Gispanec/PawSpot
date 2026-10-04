export interface TelegramWebApp {
  initData: string
  ready(): void
  expand(): void
  BackButton?: {
    show(): void
    hide(): void
    onClick(callback: () => void): void
    offClick(callback: () => void): void
  }
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp }
  }
}

export function telegramApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp
}
