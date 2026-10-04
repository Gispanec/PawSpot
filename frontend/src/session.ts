import { ApiClient, exchangeInitData, type Session } from './api'
import { telegramApp } from './telegram'

export type AppSession = {
  mode: 'telegram' | 'preview'
  profile: Session
  api: ApiClient | null
}

export async function bootstrapSession(): Promise<AppSession> {
  if (import.meta.env.DEV && import.meta.env.VITE_DEV_PREVIEW === 'true') {
    return {
      mode: 'preview',
      profile: { token: '', expires_at: '', user_public_id: '', display_name: 'Демо-пользователь' },
      api: null,
    }
  }
  const telegram = telegramApp()
  if (!telegram) {
    throw new Error('Откройте PawSpot через Telegram. Локальный просмотр описан в README.')
  }
  if (!telegram.initData) {
    throw new Error('Telegram не передал данные входа. Отправьте боту /start и откройте PawSpot через кнопку под его сообщением.')
  }
  telegram.ready()
  telegram.expand()
  const profile = await exchangeInitData(telegram.initData)
  return { mode: 'telegram', profile, api: new ApiClient(profile.token) }
}
