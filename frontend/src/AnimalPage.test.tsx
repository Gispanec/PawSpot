// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from './api'
import { AnimalPage } from './Feed'
import type { AnimalDetail, EncounterCard } from './models'

vi.mock('./Photo', () => ({ Photo: () => <div /> }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function detail(id: string, encounterId: string, cursor: string | null): AnimalDetail {
  const encounter: EncounterCard = {
    public_id: encounterId, animal_public_id: id, animal_name: id,
    species: 'dog', photo_public_id: 'photo', author_name: 'Автор', city_name: 'Tbilisi',
    comment: encounterId, observed_at: '2026-10-04T12:00:00Z',
    approximate_latitude: null, approximate_longitude: null,
    reaction_count: 0, liked_by_me: false, is_mine: true,
  }
  return {
    public_id: id, name: id, species: 'dog', city_name: 'Tbilisi',
    primary_photo_public_id: null, encounter_count: 3, photo_count: 3,
    observer_count: 1, reaction_count: 0, first_observed_at: null,
    last_observed_at: null, created_by_name: 'Автор',
    timeline: { items: [encounter], next_cursor: cursor },
  }
}

describe('Animal timeline pagination', () => {
  let root: Root
  let container: HTMLDivElement
  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
  })

  function moreButton() {
    return container.querySelector<HTMLButtonElement>('.primary-button')!
  }

  async function setup() {
    const page = deferred<AnimalDetail>()
    const next = deferred<AnimalDetail>()
    const other = deferred<AnimalDetail>()
    const request = vi.fn((path: string, _options?: RequestInit) => {
      if (path === '/animals/A?cursor=first') return page.promise
      if (path === '/animals/A?cursor=second') return next.promise
      if (path === '/animals/B?cursor=first') return other.promise
      return Promise.resolve(detail(path.slice('/animals/'.length), 'initial', 'first'))
    })
    const api = { request } as unknown as ApiClient
    await act(async () => root.render(<AnimalPage api={api} id="A" />))
    return { page, next, other, request, api }
  }

  it('sends only one request for two rapid clicks and disables pagination while pending', async () => {
    const { page, request } = await setup()
    const button = moreButton()
    await act(async () => { button.click(); button.click() })
    expect(request.mock.calls.filter(([path]) => path.includes('?cursor='))).toHaveLength(1)
    expect(moreButton().disabled).toBe(true)
    await act(async () => page.resolve(detail('A', 'page-one', 'second')))
    expect(moreButton().disabled).toBe(false)
  })

  it('appends the page once and uses the updated cursor for the next page', async () => {
    const { page, next, request } = await setup()
    await act(async () => { moreButton().click(); moreButton().click() })
    await act(async () => page.resolve(detail('A', 'page-one', 'second')))
    expect([...container.querySelectorAll('.comment')].map(item => item.textContent)).toEqual(['initial', 'page-one'])
    expect(moreButton().disabled).toBe(false)
    await act(async () => moreButton().click())
    expect(request.mock.calls.map(([path]) => path)).toEqual([
      '/animals/A', '/animals/A?cursor=first', '/animals/A?cursor=second',
    ])
    expect(moreButton().disabled).toBe(true)
    await act(async () => next.resolve(detail('A', 'page-two', null)))
    expect([...container.querySelectorAll('.comment')].map(item => item.textContent)).toEqual(['initial', 'page-one', 'page-two'])
    expect(moreButton()).toBeNull()
  })

  it('preserves the existing error message and allows pagination for the next animal', async () => {
    const { page, request, api } = await setup()
    await act(async () => moreButton().click())
    await act(async () => page.reject(new Error('Network unavailable')))
    expect(container.textContent).toBe('Не удалось открыть животное.')
    await act(async () => root.render(<AnimalPage api={api} id="B" />))
    expect(moreButton().disabled).toBe(false)
    await act(async () => moreButton().click())
    expect(request.mock.calls.at(-1)?.[0]).toBe('/animals/B?cursor=first')
  })

  it.each(['success', 'error'])('ignores a late pagination %s after switching animals', async outcome => {
    const { page, request, api } = await setup()
    await act(async () => moreButton().click())
    const signal = request.mock.calls[1][1]?.signal
    await act(async () => root.render(<AnimalPage api={api} id="B" />))
    expect(signal?.aborted).toBe(true)
    await act(async () => {
      if (outcome === 'success') page.resolve(detail('A', 'stale', null))
      else page.reject(new Error('Late error'))
    })
    expect(container.querySelector('h1')?.textContent).toBe('B')
    expect([...container.querySelectorAll('.comment')].map(item => item.textContent)).toEqual(['initial'])
    expect(moreButton().disabled).toBe(false)
    expect(container.textContent).not.toContain('Не удалось')
  })

  it('aborts pagination on unmount even if transport resolves afterward', async () => {
    const { page, request } = await setup()
    await act(async () => moreButton().click())
    const signal = request.mock.calls[1][1]?.signal
    await act(async () => root.unmount())
    expect(signal?.aborted).toBe(true)
    await act(async () => page.resolve(detail('A', 'late', 'second')))
    expect(container.textContent).toBe('')
  })

  it('does not release a new animal pagination lock when the old request finishes', async () => {
    const { page, other, request, api } = await setup()
    await act(async () => moreButton().click())
    await act(async () => root.render(<AnimalPage api={api} id="B" />))
    await act(async () => moreButton().click())
    await act(async () => page.resolve(detail('A', 'stale', null)))
    expect(moreButton().disabled).toBe(true)
    await act(async () => moreButton().click())
    expect(request.mock.calls.filter(([path]) => path === '/animals/B?cursor=first')).toHaveLength(1)
    await act(async () => other.resolve(detail('B', 'B-page', 'second')))
    expect(moreButton().disabled).toBe(false)
    expect([...container.querySelectorAll('.comment')].map(item => item.textContent)).toEqual(['initial', 'B-page'])
  })
})
