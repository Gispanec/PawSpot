export interface Session {
  token: string
  expires_at: string
  user_public_id: string
  display_name: string
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export async function exchangeInitData(initData: string): Promise<Session> {
  const response = await fetch(`${baseUrl}/api/v1/auth/telegram`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data: initData }),
    cache: 'no-store',
  })
  if (!response.ok) throw new ApiError(response.status, 'Не удалось открыть PawSpot')
  return response.json() as Promise<Session>
}

export class ApiClient {
  constructor(private readonly token: string) {}

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers)
    headers.set('Authorization', `Bearer ${this.token}`)
    const response = await fetch(`${baseUrl}/api/v1${path}`, {
      ...options,
      headers,
      cache: 'no-store',
    })
    if (!response.ok) throw new ApiError(response.status, 'Не удалось загрузить данные')
    return response.json() as Promise<T>
  }

  async image(photoId: string, variant: 'main' | 'thumbnail'): Promise<Blob> {
    const response = await fetch(`${baseUrl}/api/v1/photos/${photoId}/${variant}`, {
      headers: { Authorization: `Bearer ${this.token}` },
      cache: 'no-store',
    })
    if (!response.ok) throw new ApiError(response.status, 'Фото недоступно')
    return response.blob()
  }
}
