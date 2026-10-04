import { describe, expect, it } from 'vitest'
import { animalName, groupMarkers, mapFocusFromEncounter, type EncounterCard, type MapMarker } from './models'

describe('карточки животных на карте', () => {
  it('группирует совпадающие публичные точки без дополнительного смещения', () => {
    const first: MapMarker = {
      animal_public_id: 'one', name: 'Гиви', species: 'dog',
      photo_public_id: 'photo', encounter_public_id: 'encounter', encounter_count: 2,
      last_observed_at: '2026-10-04T12:00:00Z', city_name: 'Tbilisi',
      approximate_latitude: 41.7, approximate_longitude: 44.8,
    }
    const groups = groupMarkers([
      first,
      { ...first, animal_public_id: 'two' },
      { ...first, animal_public_id: 'three', approximate_longitude: 44.9 },
    ])
    expect(groups.map(group => group.map(marker => marker.animal_public_id))).toEqual([
      ['one', 'two'], ['three'],
    ])
    expect(groups[0][0].approximate_latitude).toBe(41.7)
  })

  it('даёт нейтральное имя животному без клички', () => {
    expect(animalName(null, 'cat')).toBe('Городской кот')
    expect(animalName(null, 'dog')).toBe('Городская собака')
  })

  it('переносит на карту только публичную координату выбранной встречи', () => {
    const encounter: EncounterCard = {
      public_id: 'encounter', animal_public_id: 'animal', animal_name: 'Гиви',
      species: 'dog', photo_public_id: 'photo', author_name: 'Автор', city_name: 'Tbilisi',
      comment: null, observed_at: '2026-10-04T12:00:00Z',
      approximate_latitude: 41.7, approximate_longitude: 44.8,
      reaction_count: 0, liked_by_me: false, is_mine: true,
    }
    const withPrivateLocation = { ...encounter, private_location: { latitude: 41.701, longitude: 44.801 } }
    expect(mapFocusFromEncounter(withPrivateLocation)).toEqual({
      animal_public_id: 'animal', encounter_public_id: 'encounter', name: 'Гиви',
      species: 'dog', photo_public_id: 'photo', city_name: 'Tbilisi',
      last_observed_at: '2026-10-04T12:00:00Z',
      approximate_latitude: 41.7, approximate_longitude: 44.8,
    })
    expect(JSON.stringify(mapFocusFromEncounter(withPrivateLocation))).not.toContain('private_location')
    expect(mapFocusFromEncounter({ ...encounter, approximate_latitude: null })).toBeNull()
    expect(mapFocusFromEncounter({ ...encounter, approximate_longitude: null })).toBeNull()
  })
})
