import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { EncounterTile } from './Feed'
import { PhotoViewer } from './PhotoViewer'
import type { EncounterCard } from './models'

const encounter: EncounterCard = {
  public_id: 'encounter', animal_public_id: 'animal', animal_name: 'Гиви',
  species: 'dog', photo_public_id: 'photo', author_name: 'Автор', city_name: 'Tbilisi',
  comment: null, observed_at: '2026-10-04T12:00:00Z',
  approximate_latitude: 41.7, approximate_longitude: 44.8,
  reaction_count: 0, liked_by_me: false, is_mine: true,
}

describe('просмотр встречи', () => {
  it('показывает отдельные действия для фото, карты и страницы встречи', () => {
    const html = renderToStaticMarkup(<EncounterTile api={null} item={encounter} />)
    expect(html).toContain('Посмотреть полное фото: Гиви')
    expect(html).toContain('Показать встречу на карте: Tbilisi')
    expect(html).toContain('Открыть встречу →')
  })

  it('не предлагает карту, если у встречи нет публичной точки', () => {
    const html = renderToStaticMarkup(<EncounterTile api={null} item={{ ...encounter, approximate_latitude: null }} />)
    expect(html).not.toContain('Показать встречу на карте')
  })

  it('открывает фото в отдельном viewer с явным закрытием', () => {
    const html = renderToStaticMarkup(<PhotoViewer api={null} id="photo" alt="Гиви" onClose={() => undefined} />)
    expect(html).toContain('role="dialog"')
    expect(html).toContain('aria-modal="true"')
    expect(html).toContain('Закрыть фото')
    expect(html).toContain('viewer-image')
  })
})
