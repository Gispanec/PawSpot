import type { MapFocus } from './models'

export type Tab = 'feed' | 'map' | 'collection' | 'profile'

export function currentPath(): string {
  return window.location.pathname
}

export function currentMapFocus(): MapFocus | null {
  return (window.history.state as { mapFocus?: MapFocus } | null)?.mapFocus ?? null
}

export function go(path: string, mapFocus?: MapFocus): void {
  if (window.location.pathname === path && !mapFocus) return
  window.history.pushState(mapFocus ? { mapFocus } : {}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function tabForPath(path: string): Tab | null {
  if (path === '/' || path === '/feed') return 'feed'
  if (path === '/map') return 'map'
  if (path === '/collection') return 'collection'
  if (path === '/profile') return 'profile'
  return null
}
