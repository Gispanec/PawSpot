import { useEffect, useState } from 'react'
import { bootstrapSession, type AppSession } from './session'
import { currentMapFocus, currentPath, go, tabForPath, type Tab } from './navigation'
import { AnimalPage, EncounterPage, Feed, Info } from './Feed'
import { CollectionPage, MapPage, ProfilePage } from './Explore'
import { telegramApp } from './telegram'
import brandSymbol from './assets/pawspot-symbol-small.svg?no-inline'
import brandLogo from './assets/pawspot-logo.svg?no-inline'
import './style.css'

const tabs: { id: Tab; label: string; icon: string; path: string }[] = [
  { id: 'feed', label: 'Лента', icon: '◒', path: '/feed' },
  { id: 'map', label: 'Карта', icon: '⌖', path: '/map' },
  { id: 'collection', label: 'Коллекция', icon: '♧', path: '/collection' },
  { id: 'profile', label: 'Профиль', icon: '☺', path: '/profile' },
]

export default function App() {
  const [session, setSession] = useState<AppSession | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [route, setRoute] = useState(() => ({ path: currentPath(), mapFocus: currentMapFocus() }))
  const { path, mapFocus } = route

  useEffect(() => {
    const onPop = () => setRoute({ path: currentPath(), mapFocus: currentMapFocus() })
    const onExpired = () => setError('Сессия истекла. Закройте и откройте PawSpot через Telegram заново.')
    window.addEventListener('popstate', onPop)
    window.addEventListener('pawspot-auth-expired', onExpired)
    void bootstrapSession().then(setSession).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Не удалось открыть PawSpot')
    })
    return () => {
      window.removeEventListener('popstate', onPop)
      window.removeEventListener('pawspot-auth-expired', onExpired)
    }
  }, [])

  useEffect(() => {
    if (session?.mode !== 'telegram') return
    const back = telegramApp()?.BackButton
    if (!back) return
    const detail = path.match(/^\/encounter\/([0-9a-f-]{36})$/i)
    const animal = path.match(/^\/animal\/([0-9a-f-]{36})$/i)
    if (!detail && !animal) { back.hide(); return }
    const onBack = () => go('/feed')
    back.show()
    back.onClick(onBack)
    return () => { back.offClick(onBack); back.hide() }
  }, [path, session])

  if (error) return <main className="center-state">
    <img className="state-logo" src={brandLogo} width={830} height={750} alt="PawSpot — городские истории о котах и собаках" />
    <h1>Не удалось войти</h1><p>{error}</p>
  </main>
  if (!session) return <main className="center-state">
    <img className="state-logo" src={brandLogo} width={830} height={750} alt="PawSpot — городские истории о котах и собаках" />
    <p>Открываем городские истории…</p>
  </main>

  const active = tabForPath(path)
  const animalId = path.match(/^\/animal\/([0-9a-f-]{36})$/i)?.[1]
  const encounterId = path.match(/^\/encounter\/([0-9a-f-]{36})$/i)?.[1]
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><img className="brand-symbol" src={brandSymbol} width={640} height={640} alt="" aria-hidden="true" /><span>PawSpot</span></div>
      <span className="topbar-note">Every city has its characters.</span>
    </header>
    <main className="page">
      {animalId ? <AnimalPage api={session.api} id={animalId} /> :
        encounterId ? <EncounterPage api={session.api} id={encounterId} /> :
        active === 'feed' ? <Feed api={session.api} preview={session.mode === 'preview'} /> :
        active === 'map' ? <MapPage api={session.api} focus={mapFocus} /> :
        active === 'collection' ? <CollectionPage api={session.api} /> :
        active === 'profile' ? <ProfilePage api={session.api} previewName={session.profile.display_name} /> :
        <Info>Этот экран скоро появится.</Info>}
    </main>
    <nav className="bottom-nav" aria-label="Основная навигация">
      {tabs.map(tab => <button key={tab.id} className={active === tab.id ? 'active' : ''} onClick={() => go(tab.path)} type="button"><span className="nav-icon">{tab.icon}</span><span>{tab.label}</span></button>)}
    </nav>
  </div>
}
