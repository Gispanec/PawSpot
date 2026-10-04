import { useEffect, useState } from 'react'
import type { ApiClient } from './api'
import { go } from './navigation'
import { Photo } from './Photo'
import { PhotoViewer } from './PhotoViewer'
import { animalName, dateLabel, mapFocusFromEncounter, type AnimalDetail, type EncounterCard, type Page } from './models'

export function toggleLike(api: ApiClient, item: EncounterCard) {
  return api.request<{ reaction_count: number; liked_by_me: boolean }>(
    `/encounters/${item.public_id}/like`,
    { method: item.liked_by_me ? 'DELETE' : 'PUT' },
  )
}

function EncounterLocation({ item }: { item: EncounterCard }) {
  const focus = mapFocusFromEncounter(item)
  const label = `📍 ${item.city_name}${focus ? ' · приблизительное место' : ''}`
  if (!focus) return <p className="location">{label}</p>
  return <button className="text-link location location-link" type="button" onClick={() => go('/map', focus)} aria-label={`Показать встречу на карте: ${item.city_name}`}>{label} →</button>
}

function ReactionControl({ item, onReaction }: { item: EncounterCard; onReaction?: () => void }) {
  if (item.is_mine) return <span className="reaction-own" title="Свою встречу оценивать нельзя">♥ {item.reaction_count}<small>Своя встреча · оценивать нельзя</small></span>
  return <button className={`reaction ${item.liked_by_me ? 'selected' : ''}`} type="button" onClick={onReaction} disabled={!onReaction} aria-label={item.liked_by_me ? 'Убрать лайк' : 'Поставить лайк'}>{item.liked_by_me ? '♥' : '♡'} {item.reaction_count}</button>
}

export function EncounterTile({ api, item, onReaction }: {
  api: ApiClient | null
  item: EncounterCard
  onReaction?: (item: EncounterCard) => void
}) {
  const name = animalName(item.animal_name, item.species)
  const [viewerOpen, setViewerOpen] = useState(false)
  return <article className="encounter-card">
    <button className="photo-button" type="button" onClick={() => setViewerOpen(true)} aria-label={`Посмотреть полное фото: ${name}`}>
      <Photo api={api} id={item.photo_public_id} alt={name} className="encounter-photo" />
    </button>
    <div className="card-body">
      <div className="card-meta"><span>{item.species === 'cat' ? 'КОТ' : 'СОБАКА'}</span><span>{dateLabel(item.observed_at)}</span></div>
      <button className="text-link card-title" type="button" onClick={() => go(`/animal/${item.animal_public_id}`)}>{name} <span aria-hidden="true">↗</span></button>
      <EncounterLocation item={item} />
      {item.comment && <p className="comment">{item.comment}</p>}
      <button className="text-link card-detail-link" type="button" onClick={() => go(`/encounter/${item.public_id}`)}>Открыть встречу →</button>
      <div className="card-footer"><span>Встретил(а) {item.author_name}</span><ReactionControl item={item} onReaction={onReaction ? () => onReaction(item) : undefined} /></div>
    </div>
    {viewerOpen && <PhotoViewer api={api} id={item.photo_public_id} alt={name} onClose={() => setViewerOpen(false)} />}
  </article>
}

export function Feed({ api, preview }: { api: ApiClient | null; preview: boolean }) {
  const [items, setItems] = useState<EncounterCard[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!api) { setLoading(false); return }
    let active = true
    void api.request<Page<EncounterCard>>('/feed').then(page => {
      if (active) { setItems(page.items); setCursor(page.next_cursor); setLoading(false) }
    }).catch(() => { if (active) { setError('Не удалось загрузить ленту.'); setLoading(false) } })
    return () => { active = false }
  }, [api])
  async function more() {
    if (!api || !cursor) return
    setLoading(true)
    try {
      const page = await api.request<Page<EncounterCard>>(`/feed?cursor=${encodeURIComponent(cursor)}`)
      setItems(current => [...current, ...page.items])
      setCursor(page.next_cursor)
    } catch { setError('Не удалось загрузить следующую страницу.') }
    finally { setLoading(false) }
  }
  async function react(item: EncounterCard) {
    if (!api) return
    try {
      const result = await toggleLike(api, item)
      setItems(current => current.map(candidate => candidate.public_id === item.public_id ? { ...candidate, ...result } : candidate))
    } catch { setError('Не удалось сохранить реакцию.') }
  }
  return <>
    <PageHeading title="Лента встреч" subtitle="Новые лица на улицах города" />
    {preview && <Info>Просмотр интерфейса. Откройте PawSpot через Telegram, чтобы увидеть настоящие встречи.</Info>}
    {error && <Info>{error}</Info>}
    {!loading && !preview && items.length === 0 && <Info>Здесь пока тихо. Первую встречу можно добавить через бот.</Info>}
    <div className="card-grid">{items.map(item => <EncounterTile key={item.public_id} item={item} api={api} onReaction={react} />)}</div>
    {loading && <Info>Загружаем встречи…</Info>}
    {cursor && !loading && <button className="primary-button" type="button" onClick={more}>Показать ещё</button>}
  </>
}

export function PageHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return <div className="page-heading"><div className="eyebrow">PAWSPOT · ТБИЛИСИ И НЕ ТОЛЬКО</div><h1>{title}</h1><p className="intro">{subtitle}</p></div>
}

export function Info({ children }: { children: React.ReactNode }) {
  return <div className="empty-card" role="status">{children}</div>
}

export function AnimalPage({ api, id }: { api: ApiClient | null; id: string }) {
  const [viewerOpen, setViewerOpen] = useState(false)
  const [animal, setAnimal] = useState<AnimalDetail | null>(null)
  const [items, setItems] = useState<EncounterCard[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!api) return
    let active = true
    void api.request<AnimalDetail>(`/animals/${id}`).then(result => {
      if (active) { setAnimal(result); setItems(result.timeline.items); setCursor(result.timeline.next_cursor) }
    }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [api, id])
  if (!api) return <Info>Откройте PawSpot через Telegram, чтобы увидеть историю.</Info>
  if (error) return <Info>Не удалось открыть животное.</Info>
  if (!animal) return <Info>Загружаем историю…</Info>
  const name = animalName(animal.name, animal.species)
  async function more() {
    if (!cursor) return
    try {
      const result = await api!.request<AnimalDetail>(`/animals/${id}?cursor=${encodeURIComponent(cursor)}`)
      setItems(current => [...current, ...result.timeline.items]); setCursor(result.timeline.next_cursor)
    } catch { setError(true) }
  }
  async function react(item: EncounterCard) {
    try {
      const result = await toggleLike(api!, item)
      setItems(current => current.map(candidate => candidate.public_id === item.public_id ? { ...candidate, ...result } : candidate))
      setAnimal(current => current ? { ...current, reaction_count: current.reaction_count + (result.liked_by_me ? 1 : -1) } : current)
    } catch { setError(true) }
  }
  return <>
    <button className="back-link" type="button" onClick={() => go('/feed')}>← К ленте</button>
    <div className="hero-card"><button className="photo-button" type="button" onClick={() => setViewerOpen(true)} aria-label={`Посмотреть полное фото: ${name}`}><Photo api={api} id={animal.primary_photo_public_id} variant="main" alt={name} className="hero-photo" /></button><div className="hero-copy"><div className="eyebrow">{animal.species === 'cat' ? 'ГОРОДСКОЙ КОТ' : 'ГОРОДСКАЯ СОБАКА'}</div><h1>{name}</h1><p>⌖ {animal.city_name || 'Город неизвестен'}</p></div></div>
    {viewerOpen && animal.primary_photo_public_id && <PhotoViewer api={api} id={animal.primary_photo_public_id} alt={name} onClose={() => setViewerOpen(false)} />}
    <div className="stats-row"><Stat number={animal.encounter_count} label="встреч" /><Stat number={animal.photo_count} label="фото" /><Stat number={animal.observer_count} label="наблюдателей" /><Stat number={animal.reaction_count} label="♥" /></div>
    <p className="muted">Первым встретил(а) {animal.created_by_name}{animal.first_observed_at ? ` · ${dateLabel(animal.first_observed_at)}` : ''}</p>
    <h2>История встреч</h2>
    <div className="card-grid">{items.map(item => <EncounterTile key={item.public_id} api={api} item={item} onReaction={react} />)}</div>
    {cursor && <button className="primary-button" type="button" onClick={more}>Больше встреч</button>}
  </>
}

export function EncounterPage({ api, id }: { api: ApiClient | null; id: string }) {
  const [viewerOpen, setViewerOpen] = useState(false)
  const [item, setItem] = useState<EncounterCard | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!api) return
    let active = true
    void api.request<EncounterCard>(`/encounters/${id}/detail`).then(result => { if (active) setItem(result) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [api, id])
  if (!api) return <Info>Откройте PawSpot через Telegram, чтобы увидеть встречу.</Info>
  if (error) return <Info>Не удалось открыть встречу.</Info>
  if (!item) return <Info>Загружаем встречу…</Info>
  const name = animalName(item.animal_name, item.species)
  async function react() {
    try {
      const result = await toggleLike(api!, item!)
      setItem(current => current ? { ...current, ...result } : current)
    } catch { setError(true) }
  }
  return <>
    <button className="back-link" type="button" onClick={() => go(`/animal/${item.animal_public_id}`)}>← К истории</button>
    <button className="photo-button detail-photo-button" type="button" onClick={() => setViewerOpen(true)} aria-label={`Посмотреть полное фото: ${name}`}><Photo api={api} id={item.photo_public_id} variant="main" alt={name} className="detail-photo" /></button>
    {viewerOpen && <PhotoViewer api={api} id={item.photo_public_id} alt={name} onClose={() => setViewerOpen(false)} />}
    <div className="eyebrow">ВСТРЕЧА · {dateLabel(item.observed_at)}</div>
    <h1>{name}</h1><EncounterLocation item={item} />
    {item.comment && <p className="detail-comment">{item.comment}</p>}
    <p className="muted">Встретил(а) {item.author_name}</p>
    <ReactionControl item={item} onReaction={react} />
    <button className="text-link section-link" type="button" onClick={() => go(`/animal/${item.animal_public_id}`)}>Вся история {name} →</button>
  </>
}

export function Stat({ number, label }: { number: number; label: string }) {
  return <div className="stat"><strong>{number}</strong><span>{label}</span></div>
}
