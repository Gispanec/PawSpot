import { useEffect, useState } from 'react'
import type { ApiClient } from './api'

export function Photo({ api, id, variant = 'thumbnail', alt, className = '' }: {
  api: ApiClient | null
  id: string | null
  variant?: 'main' | 'thumbnail'
  alt: string
  className?: string
}) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    if (!api || !id) return
    let active = true
    let objectUrl: string | null = null
    void api.image(id, variant).then(blob => {
      objectUrl = URL.createObjectURL(blob)
      if (active) setUrl(objectUrl)
      else URL.revokeObjectURL(objectUrl)
    }).catch(() => { if (active) setUrl(null) })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [api, id, variant])
  if (!url) return <div className={`photo-placeholder ${className}`} aria-label={alt}>🐾</div>
  return <img className={className} src={url} alt={alt} onError={() => setUrl(null)} />
}
