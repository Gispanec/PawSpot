export type Tab = 'feed' | 'map' | 'collection' | 'profile'

export function currentPath(): string {
  return window.location.pathname
}

export function go(path: string): void {
  if (window.location.pathname === path) return
  window.history.pushState({}, '', path)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function tabForPath(path: string): Tab | null {
  if (path === '/' || path === '/feed') return 'feed'
  if (path === '/map') return 'map'
  if (path === '/collection') return 'collection'
  if (path === '/profile') return 'profile'
  return null
}
