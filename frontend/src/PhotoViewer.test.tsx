// @vitest-environment happy-dom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PhotoViewer } from './PhotoViewer'

function ViewerExample() {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" onClick={() => setOpen(true)}>Открыть фото</button>
    {open && <PhotoViewer api={null} id="photo" alt="Кот" onClose={() => setOpen(false)} />}
  </>
}

describe('Photo viewer interactions', () => {
  let root: Root
  let container: HTMLDivElement
  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    document.body.style.overflow = 'auto'
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    document.body.style.overflow = ''
    vi.unstubAllGlobals()
  })

  async function open() {
    await act(async () => root.render(<ViewerExample />))
    const trigger = container.querySelector('button')!
    trigger.focus()
    await act(async () => trigger.click())
    expect(document.body.style.overflow).toBe('hidden')
    expect(document.activeElement).toBe(container.querySelector('.viewer-close'))
    return trigger
  }

  it.each(['button', 'escape', 'backdrop'])('closes by %s and restores scrolling/focus', async action => {
    const trigger = await open()
    await act(async () => {
      if (action === 'escape') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      else (container.querySelector(action === 'button' ? '.viewer-close' : '.photo-viewer') as HTMLElement).click()
    })
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    expect(document.body.style.overflow).toBe('auto')
    expect(document.activeElement).toBe(trigger)
  })

  it('does not close when touching the image and restores scrolling on unmount', async () => {
    await open()
    await act(async () => (container.querySelector('.viewer-image') as HTMLElement).click())
    expect(container.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.body.style.overflow).toBe('hidden')
    await act(async () => root.unmount())
    expect(document.body.style.overflow).toBe('auto')
  })
})
