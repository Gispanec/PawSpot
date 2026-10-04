import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { EncounterTile, toggleLike } from './Feed'
import { PhotoViewer } from './PhotoViewer'
import type { EncounterCard } from './models'
import type { ApiClient } from './api'

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

  it('объясняет запрет лайка собственной встречи без активной кнопки', () => {
    const html = renderToStaticMarkup(<EncounterTile api={null} item={encounter} onReaction={() => undefined} />)
    expect(html).toContain('Своя встреча · оценивать нельзя')
    expect(html).not.toContain('aria-label="Поставить лайк"')
  })

  it('показывает интерактивные состояния лайка чужой встречи', () => {
    const other = { ...encounter, is_mine: false }
    const empty = renderToStaticMarkup(<EncounterTile api={null} item={other} onReaction={() => undefined} />)
    expect(empty).toContain('aria-label="Поставить лайк"')
    expect(empty).toContain('♡ 0')
    const liked = renderToStaticMarkup(<EncounterTile api={null} item={{ ...other, liked_by_me: true, reaction_count: 1 }} onReaction={() => undefined} />)
    expect(liked).toContain('aria-label="Убрать лайк"')
    expect(liked).toContain('♥ 1')
  })

  it('ставит и снимает лайк через существующий API', async () => {
    const request = vi.fn().mockResolvedValueOnce({ liked_by_me: true, reaction_count: 1 }).mockResolvedValueOnce({ liked_by_me: false, reaction_count: 0 })
    const api = { request } as unknown as ApiClient
    const liked = await toggleLike(api, { ...encounter, is_mine: false })
    expect(request).toHaveBeenNthCalledWith(1, '/encounters/encounter/like', { method: 'PUT' })
    await toggleLike(api, { ...encounter, ...liked, is_mine: false })
    expect(request).toHaveBeenNthCalledWith(2, '/encounters/encounter/like', { method: 'DELETE' })
  })
})
