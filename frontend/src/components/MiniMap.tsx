/**
 * The viewer's small map (MAP-02): where one item was taken. Still - no
 * dragging or zooming in the info panel - and a click opens the full map
 * there.
 */

import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

import { L, pinIcon, tiles } from '@/lib/leaflet'

export function MiniMap({ lat, lon }: { readonly lat: number; readonly lon: number }) {
  const box = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (!box.current) return
    const map = L.map(box.current, {
      center: [lat, lon],
      zoom: 13,
      zoomControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      touchZoom: false,
    })
    map.attributionControl.setPrefix(false)
    tiles().addTo(map)
    L.marker([lat, lon], { icon: pinIcon(), interactive: false }).addTo(map)
    return () => {
      map.remove()
    }
  }, [lat, lon])

  return (
    <div
      ref={box}
      className="mini-map live"
      role="link"
      tabIndex={0}
      title="Open on the map"
      onClick={() => navigate(`/map?at=${lat},${lon}`)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') navigate(`/map?at=${lat},${lon}`)
      }}
    />
  )
}
