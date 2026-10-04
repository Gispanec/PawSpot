import { useEffect, useState } from 'react'
import { bootstrapSession, type AppSession } from './session'
import { currentPath, go, tabForPath, type Tab } from './navigation'
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
  const [path, setPath] = useState(currentPath)

  useEffect(() => {
    const onPop = () => setPath(currentPath())
    window.addEventListener('popstate', onPop)
    void bootstrapSession().then(setSession).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Не удалось открыть PawSpot')
    })
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  if (error) return <main className="center-state"><div className="brand">🐾 PawSpot</div><h1>Не удалось войти</h1><p>{error}</p></main>
  if (!session) return <main className="center-state"><div className="brand">🐾 PawSpot</div><p>Открываем городские истории…</p></main>

  const active = tabForPath(path)
  return <div className="app-shell">
    <header className="topbar"><div className="brand">🐾 PawSpot</div><span className="topbar-note">Every city has its characters.</span></header>
    <main className="page">
      <div className="eyebrow">ГОРОДСКИЕ ПЕРСОНАЖИ</div>
      <h1>{tabs.find(tab => tab.id === active)?.label ?? 'PawSpot'}</h1>
      <p className="intro">{session.mode === 'preview' ? 'Демонстрационный просмотр интерфейса.' : `Привет, ${session.profile.display_name}!`}</p>
      <div className="empty-card">Скоро здесь появятся встречи и истории животных.</div>
    </main>
    <nav className="bottom-nav" aria-label="Основная навигация">
      {tabs.map(tab => <button key={tab.id} className={active === tab.id ? 'active' : ''} onClick={() => go(tab.path)} type="button"><span className="nav-icon">{tab.icon}</span><span>{tab.label}</span></button>)}
    </nav>
  </div>
}
