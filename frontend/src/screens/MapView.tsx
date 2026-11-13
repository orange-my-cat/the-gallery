/**
 * The map (MAP-01): every geotagged item, clustered.
 *
 * Not in the Claude Design handoff beyond its nav item. All points come in one
 * request (HLD "Map view") and supercluster groups them in the browser, so
 * panning and zooming never wait on the server. A cluster shows its newest
 * photo and a count; selecting one lists its items beside the map, from where
 * they open in the standard grid (the timeline narrowed to that area) or one
 * at a time in the viewer.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import Supercluster from 'supercluster'

import { Icon } from '@/components/Icon'
import { count, plural } from '@/lib/format'
import { areaAround, L, tiles } from '@/lib/leaflet'
import { useMapPoints } from '@/lib/queries'

interface PointProps {
  readonly id: string
  readonly thumb: string | null
}
interface ClusterProps {
  thumb: string | null
}
interface Selection {
  readonly items: readonly { id: string; thumb: string | null; at: readonly [number, number] }[]
  readonly area: string
}

const VIEW_KEY = 'gallery.map.view'
const PANEL_MAX = 120

function savedView(): { lat: number; lon: number; zoom: number } | null {
  try {
    const raw = sessionStorage.getItem(VIEW_KEY)
    return raw ? (JSON.parse(raw) as { lat: number; lon: number; zoom: number }) : null
  } catch {
    return null
  }
}

function saveView(map: L.Map) {
  try {
    const c = map.getCenter()
    sessionStorage.setItem(VIEW_KEY, JSON.stringify({ lat: c.lat, lon: c.lng, zoom: map.getZoom() }))
  } catch {
    // A private window: the map simply opens on everything next time.
  }
}

function tileHtml(thumb: string | null, n: number): string {
  const img = thumb ? `<img src="${thumb}" alt="" loading="lazy" />` : ''
  const badge = n > 1 ? `<span>${n > 999 ? `${Math.round(n / 1000)}k` : n}</span>` : ''
  return `<div class="map-tile${n > 1 ? ' many' : ''}">${img}${badge}</div>`
}

export function MapView() {
  const points = useMapPoints().data
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const box = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const fitted = useRef(false)
  const [selected, setSelected] = useState<Selection | null>(null)

  const index = useMemo(() => {
    const cluster = new Supercluster<PointProps, ClusterProps>({
      radius: 70,
      maxZoom: 17,
      map: (p) => ({ thumb: p.thumb }),
      reduce: (acc, p) => {
        if (!acc.thumb) acc.thumb = p.thumb
      },
    })
    cluster.load(
      (points?.points ?? []).map(([id, lat, lon, thumb]) => ({
        type: 'Feature' as const,
        properties: { id, thumb },
        geometry: { type: 'Point' as const, coordinates: [lon, lat] },
      })),
    )
    return cluster
  }, [points])

  // The map itself, once.
  useEffect(() => {
    if (!box.current || map.current) return
    const m = L.map(box.current, { worldCopyJump: true, minZoom: 2, zoomControl: false })
    L.control.zoom({ position: 'topleft' }).addTo(m) // clear of the selection panel
    tiles().addTo(m)
    layer.current = L.layerGroup().addTo(m)
    const at = params.get('at')?.split(',').map(Number)
    const saved = savedView()
    if (at && at.length === 2 && at.every(Number.isFinite)) {
      m.setView([at[0] as number, at[1] as number], 15)
      fitted.current = true
    } else if (saved) {
      m.setView([saved.lat, saved.lon], saved.zoom)
      fitted.current = true
    } else {
      m.setView([20, 0], 2)
    }
    m.on('moveend', () => saveView(m))
    map.current = m
    return () => {
      m.remove()
      map.current = null
    }
    // Created once: ?at= is read on arrival only.
  }, [])

  // Open on everything, the first time the points arrive.
  useEffect(() => {
    const m = map.current
    if (!m || fitted.current || !points?.points.length) return
    const bounds = L.latLngBounds(points.points.map(([, lat, lon]) => [lat, lon] as [number, number]))
    m.fitBounds(bounds, { padding: [48, 48], maxZoom: 14 })
    fitted.current = true
  }, [points])

  // Clusters for the view, redrawn on every move.
  useEffect(() => {
    const m = map.current
    const group = layer.current
    if (!m || !group) return
    const draw = () => {
      const b = m.getBounds()
      const wide = b.getEast() - b.getWest() >= 360
      const bbox: [number, number, number, number] = [
        wide ? -180 : Math.max(-180, b.getWest()),
        Math.max(-85, b.getSouth()),
        wide ? 180 : Math.min(180, b.getEast()),
        Math.min(85, b.getNorth()),
      ]
      group.clearLayers()
      for (const c of index.getClusters(bbox, Math.round(m.getZoom()))) {
        const [lon, lat] = c.geometry.coordinates as [number, number]
        const isCluster = 'cluster' in c.properties && c.properties.cluster
        const n = isCluster ? (c.properties as Supercluster.ClusterProperties).point_count : 1
        const icon = L.divIcon({ className: 'map-tile-icon', html: tileHtml(c.properties.thumb, n), iconSize: [56, 56], iconAnchor: [28, 28] })
        const marker = L.marker([lat, lon], { icon, keyboard: true, title: `${n} ${n === 1 ? 'item' : 'items'}` })
        marker.on('click', () => {
          const leaves = isCluster
            ? index.getLeaves(c.id as number, Infinity)
            : [c as Supercluster.PointFeature<PointProps>]
          const items = leaves.map((leaf) => {
            const [x, y] = leaf.geometry.coordinates as [number, number]
            return { id: leaf.properties.id, thumb: leaf.properties.thumb, at: [y, x] as const }
          })
          setSelected({ items, area: areaAround(items.map((i) => i.at)) })
        })
        group.addLayer(marker)
      }
    }
    draw()
    m.on('moveend', draw)
    return () => {
      m.off('moveend', draw)
    }
  }, [index])

  const zoomIn = () => {
    const m = map.current
    if (!m || !selected) return
    const [s, w, n, e] = selected.area.split(',').map(Number) as [number, number, number, number]
    m.fitBounds([[s, w], [n, e]], { padding: [64, 64], maxZoom: 17 })
  }

  const none = points && points.count === 0

  return (
    <div className="map-screen" data-screen-label="Map">
      <div ref={box} className="map-canvas" />

      {none && (
        <div className="map-empty">
          <Icon name="map" size={22} />
          <div className="lines">
            <b>No photos with a location yet</b>
            <span>Photos and videos whose files carry GPS positions appear here, grouped by where they were taken.</span>
          </div>
        </div>
      )}

      {selected && (
        <aside className="map-panel" aria-label="Selected items">
          <div className="head">
            <div className="lines">
              <b className="num">{`${count(selected.items.length)} ${plural(selected.items.length, 'item')}`}</b>
              <span className="muted">Taken here</span>
            </div>
            <button type="button" className="icon-btn" title="Close" onClick={() => setSelected(null)}>
              <Icon name="x" size={16} />
            </button>
          </div>
          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={() => navigate(`/?area=${selected.area}`)}>
              <Icon name="images" size={15} />
              Open in timeline
            </button>
            <button type="button" className="btn btn-secondary" onClick={zoomIn}>
              <Icon name="zoom-in" size={15} />
              Zoom in
            </button>
          </div>
          <div className="grid">
            {selected.items.slice(0, PANEL_MAX).map((item) => (
              <button
                key={item.id}
                type="button"
                className="cell"
                onClick={() => navigate(`/photo/${item.id}?area=${selected.area}`)}
                style={item.thumb ? { backgroundImage: `url("${item.thumb}")` } : undefined}
                aria-label="Open"
              />
            ))}
          </div>
          {selected.items.length > PANEL_MAX && (
            <p className="muted more">{`and ${count(selected.items.length - PANEL_MAX)} more in the timeline`}</p>
          )}
        </aside>
      )}
    </div>
  )
}
