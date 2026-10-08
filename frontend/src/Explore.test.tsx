// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from './api'
import type { MapMarker } from './models'
import { MapPage } from './Explore'

vi.mock('./Photo', () => ({ Photo: () => <div /> }))

const leaflet = vi.hoisted(() => ({
  listeners: new Map<string, () => void>(),
  bounds: { south: 41.7, west: 44.8, north: 41.8, east: 44.9 },
  circles: [] as { point: number[]; click?: () => void }[],
  remove: vi.fn(),
}))
vi.mock('leaflet', () => ({ default: {
  map: () => {
    const map = {
      setView: () => map,
      getBounds: () => ({
        getSouth: () => leaflet.bounds.south, getWest: () => leaflet.bounds.west,
        getNorth: () => leaflet.bounds.north, getEast: () => leaflet.bounds.east,
      }),
      on: (name: string, callback: () => void) => leaflet.listeners.set(name, callback),
      off: (name: string) => leaflet.listeners.delete(name),
      remove: leaflet.remove,
    }
    return map
  },
  tileLayer: () => ({ addTo: vi.fn() }),
  layerGroup: () => {
    leaflet.circles = []
    const layer = { addTo: () => layer, remove: vi.fn() }
    return layer
  },
  circleMarker: (point: number[]) => {
    const circle: { point: number[]; click?: () => void } = { point }
    leaflet.circles.push(circle)
    const marker = {
      on: (_: string, callback: () => void) => { circle.click = callback; return marker },
      addTo: () => marker,
    }
    return marker
  },
} }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function animal(name: string, latitude: number): MapMarker {
  return {
    animal_public_id: name, encounter_public_id: name, name, species: 'cat',
    city_name: 'Tbilisi', photo_public_id: 'photo', encounter_count: 1,
    approximate_latitude: latitude, approximate_longitude: 44.85,
    last_observed_at: '2026-10-08T12:00:00Z',
  }
}

describe('Map viewport requests', () => {
  let root: Root
  let container: HTMLDivElement
  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    leaflet.listeners.clear()
    leaflet.bounds = { south: 41.7, west: 44.8, north: 41.8, east: 44.9 }
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
  })

  it('keeps B markers when A resolves after B, even if transport ignores abort', async () => {
    const a = deferred<MapMarker[]>(), b = deferred<MapMarker[]>()
    const request = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    await act(async () => root.render(<MapPage api={{ request } as unknown as ApiClient} focus={null} />))
    leaflet.bounds = { ...leaflet.bounds, south: 41.75 }
    await act(async () => leaflet.listeners.get('moveend')!())
    expect(request.mock.calls[0][0]).toContain('south=41.7&')
    expect(request.mock.calls[1][0]).toContain('south=41.75&')
    await act(async () => b.resolve([animal('B', 41.76)]))
    await act(async () => a.resolve([animal('A', 41.72)]))
    expect(leaflet.circles.map(circle => circle.point[0])).toEqual([41.76])
    await act(async () => leaflet.circles[0].click!())
    expect(container.querySelector('.map-selection')?.textContent).toContain('B')
    expect(container.querySelector('.map-selection')?.textContent).not.toContain('A')
    expect(request.mock.calls[0][1].signal.aborted).toBe(true)
  })

  it('does not show cancellation errors and keeps the newest error state', async () => {
    const a = deferred<MapMarker[]>(), b = deferred<MapMarker[]>()
    const request = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    await act(async () => root.render(<MapPage api={{ request } as unknown as ApiClient} focus={null} />))
    await act(async () => leaflet.listeners.get('moveend')!())
    await act(async () => a.reject(new DOMException('Aborted', 'AbortError')))
    expect(container.textContent).not.toContain('Не удалось загрузить метки')
    await act(async () => b.reject(new Error('Network unavailable')))
    expect(container.textContent).toContain('Не удалось загрузить метки')
  })

  it('invalidates a pending request when viewport becomes too wide', async () => {
    const a = deferred<MapMarker[]>()
    const request = vi.fn().mockReturnValue(a.promise)
    await act(async () => root.render(<MapPage api={{ request } as unknown as ApiClient} focus={null} />))
    leaflet.bounds.north = 45
    await act(async () => leaflet.listeners.get('moveend')!())
    await act(async () => a.resolve([animal('A', 41.72)]))
    expect(leaflet.circles).toHaveLength(0)
    expect(request).toHaveBeenCalledTimes(1)
    expect(request.mock.calls[0][1].signal.aborted).toBe(true)
  })

  it('ignores a stale error after the newest viewport has succeeded', async () => {
    const a = deferred<MapMarker[]>(), b = deferred<MapMarker[]>()
    const request = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    await act(async () => root.render(<MapPage api={{ request } as unknown as ApiClient} focus={null} />))
    await act(async () => leaflet.listeners.get('moveend')!())
    await act(async () => b.resolve([animal('B', 41.76)]))
    await act(async () => a.reject(new Error('Late error from A')))
    expect(container.textContent).not.toContain('Не удалось загрузить метки')
    expect(leaflet.circles.map(circle => circle.point[0])).toEqual([41.76])
  })

  it('keeps the latest species filter and ignores responses from the previous filter', async () => {
    const a = deferred<MapMarker[]>(), b = deferred<MapMarker[]>()
    const request = vi.fn().mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
    await act(async () => root.render(<MapPage api={{ request } as unknown as ApiClient} focus={null} />))
    const filter = [...container.querySelectorAll('button')].find(button => button.textContent === 'Коты')!
    await act(async () => filter.click())
    expect(request.mock.calls[1][0]).toContain('species=cat')
    expect(request.mock.calls[0][1].signal.aborted).toBe(true)
    await act(async () => b.resolve([animal('B', 41.76)]))
    await act(async () => a.resolve([animal('A', 41.72)]))
    expect(leaflet.circles.map(circle => circle.point[0])).toEqual([41.76])
  })

  it('aborts and ignores responses after component unmount', async () => {
    const a = deferred<MapMarker[]>()
    const request = vi.fn().mockReturnValue(a.promise)
    await act(async () => root.render(<MapPage api={{ request } as unknown as ApiClient} focus={null} />))
    await act(async () => root.unmount())
    expect(leaflet.listeners.has('moveend')).toBe(false)
    expect(request.mock.calls[0][1].signal.aborted).toBe(true)
    const circles = leaflet.circles
    await act(async () => a.resolve([animal('A', 41.72)]))
    expect(leaflet.circles).toBe(circles)
    expect(container.textContent).toBe('')
  })
})
