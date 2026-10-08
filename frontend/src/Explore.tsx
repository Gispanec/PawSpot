import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { ApiClient } from './api'
import { Info, PageHeading, Stat } from './Feed'
import { animalName, dateLabel, groupMarkers, type MapFocus, type MapMarker } from './models'
import { go } from './navigation'
import { Photo } from './Photo'

interface CollectionCard {
  animal_public_id: string
  name: string | null
  species: 'cat' | 'dog'
  photo_public_id: string | null
  own_encounter_count: number
  last_observed_at: string
}

interface ProfileView {
  display_name: string
  joined_at: string
  unique_animals: number
  encounter_count: number
  cats: number
  dogs: number
}

interface MapSelection {
  items: (MapMarker | MapFocus)[]
  focusedEncounter: boolean
}

export function MapPage({ api, focus }: { api: ApiClient | null; focus: MapFocus | null }) {
  const element = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<L.Map | null>(null)
  const [markers, setMarkers] = useState<MapMarker[]>([])
  const [selected, setSelected] = useState<MapSelection | null>(null)
  const [species, setSpecies] = useState<'all' | 'cat' | 'dog'>('all')
  const [error, setError] = useState(false)
  const visibleFocus = focus && (species === 'all' || species === focus.species) ? focus : null
  useEffect(() => {
    if (!element.current) return
    const instance = L.map(element.current).setView([41.7151, 44.8271], 12)
    const tileUrl = import.meta.env.VITE_MAP_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
    L.tileLayer(tileUrl, {
      attribution: import.meta.env.VITE_MAP_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
      maxZoom: 18,
    }).addTo(instance)
    setMap(instance)
    return () => { instance.remove() }
  }, [])
  useEffect(() => {
    if (!map || !api) return
    let active = true
    let generation = 0
    let controller: AbortController | null = null
    const load = () => {
      const requestGeneration = ++generation
      controller?.abort()
      const request = new AbortController()
      controller = request
      const bounds = map.getBounds()
      const values = {
        south: bounds.getSouth(), west: bounds.getWest(),
        north: bounds.getNorth(), east: bounds.getEast(),
      }
      if (values.north - values.south > 2 || values.east - values.west > 2) {
        setMarkers([]); setError(false); return
      }
      const query = new URLSearchParams(Object.entries(values).map(([key, value]) => [key, String(value)]))
      if (species !== 'all') query.set('species', species)
      const isCurrent = () => active && generation === requestGeneration && !request.signal.aborted
      void api.request<MapMarker[]>(`/map/animals?${query}`, { signal: request.signal })
        .then(result => { if (isCurrent()) { setMarkers(result); setError(false) } })
        .catch(() => { if (isCurrent()) setError(true) })
    }
    map.on('moveend', load)
    load()
    return () => { active = false; controller?.abort(); map.off('moveend', load) }
  }, [map, api, species])
  useEffect(() => {
    if (!map || !focus) return
    map.setView([focus.approximate_latitude, focus.approximate_longitude], 15)
    setSelected({ items: [focus], focusedEncounter: true })
  }, [map, focus])
  useEffect(() => {
    if (!map) return
    const layer = L.layerGroup().addTo(map)
    for (const group of groupMarkers(markers)) {
      const first = group[0]
      const highlighted = selected?.items.some(item => item.animal_public_id === first.animal_public_id && item.approximate_latitude === first.approximate_latitude && item.approximate_longitude === first.approximate_longitude) ?? false
      L.circleMarker([first.approximate_latitude, first.approximate_longitude], {
        radius: highlighted ? 15 : group.length > 1 ? 12 : 9,
        color: '#fff', weight: highlighted ? 3 : 2, fillColor: highlighted ? '#d8794a' : '#3f7755', fillOpacity: 1,
      }).on('click', () => setSelected({ items: group, focusedEncounter: false })).addTo(layer)
    }
    if (visibleFocus) {
      L.circleMarker([visibleFocus.approximate_latitude, visibleFocus.approximate_longitude], {
        radius: 15, color: '#fff', weight: 3, fillColor: '#d8794a', fillOpacity: 1,
      }).on('click', () => setSelected({ items: [visibleFocus], focusedEncounter: true })).addTo(layer)
    }
    return () => { layer.remove() }
  }, [map, markers, selected, visibleFocus])
  return <>
    <PageHeading title="Карта" subtitle="Город полон знакомых мордочек" />
    <div className="filter-row" aria-label="Фильтр животных">
      {([['all', 'Все'], ['cat', 'Коты'], ['dog', 'Собаки']] as const).map(([value, label]) => <button key={value} className={species === value ? 'selected' : ''} type="button" onClick={() => { setSpecies(value); setSelected(null) }}>{label}</button>)}
    </div>
    <div ref={element} className="map-canvas" aria-label="Карта приблизительных мест встреч" />
    <p className="map-note">Метки показывают приблизительную область встречи, а не точное место.</p>
    {error && <Info>Не удалось загрузить метки. Передвиньте карту и попробуйте ещё раз.</Info>}
    {!api && <Info>Откройте PawSpot через Telegram, чтобы увидеть животных на карте.</Info>}
    {api && !error && markers.length === 0 && !visibleFocus && <Info>В этой области пока нет встреч. Попробуйте переместить карту.</Info>}
    {selected && <div className="map-selection" role="region" aria-label="Выбранная точка на карте">
      <div className="map-selection-heading"><strong>{selected.focusedEncounter ? 'Выбранная встреча' : 'Здесь встречали'}</strong><button type="button" onClick={() => setSelected(null)} aria-label="Закрыть карточку на карте">✕</button></div>
      <div className="map-selection-list">{selected.items.map(item => <button type="button" className="map-animal" key={item.encounter_public_id} onClick={() => go(selected.focusedEncounter ? `/encounter/${item.encounter_public_id}` : `/animal/${item.animal_public_id}`)}><Photo api={api} id={item.photo_public_id} alt={animalName(item.name, item.species)} className="map-thumbnail" /><span><strong>{animalName(item.name, item.species)}</strong><small>{item.species === 'cat' ? 'Кот' : 'Собака'} · {item.city_name}</small><small>{'encounter_count' in item && !selected.focusedEncounter ? `${item.encounter_count} встреч · ` : ''}{dateLabel(item.last_observed_at)}</small></span><b>Открыть →</b></button>)}</div>
    </div>}
  </>
}

export function CollectionPage({ api }: { api: ApiClient | null }) {
  const [items, setItems] = useState<CollectionCard[] | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!api) return
    let active = true
    void api.request<CollectionCard[]>('/collection').then(result => { if (active) setItems(result) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [api])
  return <>
    <PageHeading title="Коллекция" subtitle="Персонажи, которых встретили вы" />
    {!api && <Info>Откройте PawSpot через Telegram, чтобы увидеть свою коллекцию.</Info>}
    {api && !items && !error && <Info>Загружаем коллекцию…</Info>}
    {error && <Info>Не удалось загрузить коллекцию.</Info>}
    {items?.length === 0 && <Info>Здесь появятся животные после вашей первой встречи через бот.</Info>}
    <div className="collection-grid">{items?.map(item => <button className="collection-card" type="button" key={item.animal_public_id} onClick={() => go(`/animal/${item.animal_public_id}`)}><Photo api={api} id={item.photo_public_id} alt={animalName(item.name, item.species)} className="collection-photo" /><span className="collection-copy"><strong>{animalName(item.name, item.species)}</strong><small>{item.species === 'cat' ? 'Кот' : 'Собака'} · ваших встреч: {item.own_encounter_count}</small><small>Последняя: {dateLabel(item.last_observed_at)}</small></span></button>)}</div>
  </>
}

export function ProfilePage({ api, previewName }: { api: ApiClient | null; previewName: string }) {
  const [profile, setProfile] = useState<ProfileView | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!api) return
    let active = true
    void api.request<ProfileView>('/users/me/profile').then(result => { if (active) setProfile(result) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [api])
  return <>
    <PageHeading title="Ваш профиль" subtitle="Ваша история PawSpot" />
    <div className="profile-card"><div className="avatar">🐾</div><div><strong>{profile?.display_name || previewName}</strong><p>С нами {profile ? `с ${dateLabel(profile.joined_at)}` : 'в демо-режиме'}</p></div></div>
    {error && <Info>Не удалось загрузить статистику.</Info>}
    {api && !profile && !error && <Info>Загружаем статистику…</Info>}
    {profile && <div className="stats-row profile-stats"><Stat number={profile.unique_animals} label="персонажей" /><Stat number={profile.encounter_count} label="встреч" /><Stat number={profile.cats} label="котов" /><Stat number={profile.dogs} label="собак" /></div>}
    <button className="primary-button" type="button" onClick={() => go('/collection')}>Моя коллекция →</button>
  </>
}
