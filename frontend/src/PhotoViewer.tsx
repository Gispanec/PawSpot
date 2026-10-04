import { useEffect, useRef } from 'react'
import type { ApiClient } from './api'
import { Photo } from './Photo'

export function PhotoViewer({ api, id, alt, onClose }: {
  api: ApiClient | null
  id: string
  alt: string
  onClose: () => void
}) {
  const closeButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    document.body.style.overflow = 'hidden'
    closeButton.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') { event.preventDefault(); closeButton.current?.focus() }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
      previousFocus?.focus()
    }
  }, [onClose])

  return <div className="photo-viewer" role="dialog" aria-modal="true" aria-label={`Фото: ${alt}`} onClick={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <button ref={closeButton} className="viewer-close" type="button" onClick={onClose} aria-label="Закрыть фото">✕</button>
    <Photo api={api} id={id} variant="main" alt={alt} className="viewer-image" />
  </div>
}
