import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { ApiClient } from './api'
import { Info, PageHeading, Stat } from './Feed'
import { animalName, dateLabel, groupMarkers, type MapMarker } from './models'
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

export function MapPage({ api }: { api: ApiClient | null }) {
  const element = useRef<HTMLDivElement>(null)
  const [map, setMap] = useState<L.Map | null>(null)
  const [markers, setMarkers] = useState<MapMarker[]>([])
  const [selected, setSelected] = useState<MapMarker[]>([])
  const [species, setSpecies] = useState<'all' | 'cat' | 'dog'>('all')
  const [error, setError] = useState(false)
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
    const load = () => {
      const bounds = map.getBounds()
      const values = {
        south: bounds.getSouth(), west: bounds.getWest(),
        north: bounds.getNorth(), east: bounds.getEast(),
      }
      if (values.north - values.south > 2 || values.east - values.west > 2) {
        setMarkers([]); return
      }
      const query = new URLSearchParams(Object.entries(values).map(([key, value]) => [key, String(value)]))
      if (species !== 'all') query.set('species', species)
      void api.request<MapMarker[]>(`/map/animals?${query}`).then(result => { if (active) { setMarkers(result); setError(false) } }).catch(() => { if (active) setError(true) })
    }
    map.on('moveend', load)
    load()
    return () => { active = false; map.off('moveend', load) }
  }, [map, api, species])
  useEffect(() => {
    if (!map) return
    const layer = L.layerGroup().addTo(map)
    for (const group of groupMarkers(markers)) {
      const first = group[0]
      L.circleMarker([first.approximate_latitude, first.approximate_longitude], {
        radius: group.length > 1 ? 12 : 9,
        color: '#fff', weight: 2, fillColor: '#3f7755', fillOpacity: 1,
      }).on('click', () => setSelected(group)).addTo(layer)
    }
    return () => { layer.remove() }
  }, [map, markers])
  return <>
    <PageHeading title="Карта" subtitle="Город полон знакомых мордочек" />
    <div className="filter-row" aria-label="Фильтр животных">
      {([['all', 'Все'], ['cat', 'Коты'], ['dog', 'Собаки']] as const).map(([value, label]) => <button key={value} className={species === value ? 'selected' : ''} type="button" onClick={() => { setSpecies(value); setSelected([]) }}>{label}</button>)}
    </div>
    <div ref={element} className="map-canvas" aria-label="Карта приблизительных мест встреч" />
    <p className="map-note">Метки показывают приблизительную область встречи, а не точное место.</p>
    {error && <Info>Не удалось загрузить метки. Передвиньте карту и попробуйте ещё раз.</Info>}
    {!api && <Info>Откройте PawSpot через Telegram, чтобы увидеть животных на карте.</Info>}
    {api && !error && markers.length === 0 && <Info>В этой области пока нет встреч. Попробуйте переместить карту.</Info>}
    {selected.length > 0 && <div className="map-selection"><h2>Здесь встречали</h2>{selected.map(item => <button type="button" className="map-animal" key={item.animal_public_id} onClick={() => go(`/animal/${item.animal_public_id}`)}><Photo api={api} id={item.photo_public_id} alt={animalName(item.name, item.species)} className="map-thumbnail" /><span><strong>{animalName(item.name, item.species)}</strong><small>{item.species === 'cat' ? 'Кот' : 'Собака'} · {item.city_name}</small><small>{item.encounter_count} встреч · {dateLabel(item.last_observed_at)}</small></span><span>→</span></button>)}</div>}
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
