export interface EncounterCard {
  public_id: string
  animal_public_id: string
  animal_name: string | null
  species: 'cat' | 'dog'
  photo_public_id: string
  author_name: string
  city_name: string
  comment: string | null
  observed_at: string
  approximate_latitude: number | null
  approximate_longitude: number | null
  reaction_count: number
  liked_by_me: boolean
  is_mine: boolean
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface AnimalDetail {
  public_id: string
  name: string | null
  species: 'cat' | 'dog'
  city_name: string | null
  primary_photo_public_id: string | null
  encounter_count: number
  photo_count: number
  observer_count: number
  reaction_count: number
  first_observed_at: string | null
  last_observed_at: string | null
  created_by_name: string
  timeline: Page<EncounterCard>
}

export interface MapMarker {
  animal_public_id: string
  name: string | null
  species: 'cat' | 'dog'
  photo_public_id: string
  encounter_public_id: string
  encounter_count: number
  last_observed_at: string
  city_name: string
  approximate_latitude: number
  approximate_longitude: number
}

export type MapFocus = Omit<MapMarker, 'encounter_count'>

export function mapFocusFromEncounter(item: EncounterCard): MapFocus | null {
  if (item.approximate_latitude === null || item.approximate_longitude === null) return null
  return {
    animal_public_id: item.animal_public_id,
    encounter_public_id: item.public_id,
    name: item.animal_name,
    species: item.species,
    photo_public_id: item.photo_public_id,
    last_observed_at: item.observed_at,
    city_name: item.city_name,
    approximate_latitude: item.approximate_latitude,
    approximate_longitude: item.approximate_longitude,
  }
}

export function groupMarkers(markers: MapMarker[]): MapMarker[][] {
  const groups = new Map<string, MapMarker[]>()
  for (const marker of markers) {
    const key = `${marker.approximate_latitude}:${marker.approximate_longitude}`
    groups.set(key, [...(groups.get(key) || []), marker])
  }
  return [...groups.values()]
}

export function animalName(name: string | null, species: string): string {
  return name || (species === 'cat' ? 'Городской кот' : 'Городская собака')
}

export function dateLabel(value: string): string {
  return new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(value))
}
