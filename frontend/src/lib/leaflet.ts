/**
 * Leaflet, set up once for the map and the viewer's mini map.
 *
 * Tiles are OpenStreetMap's public servers (the owner's choice, 2026-11-14):
 * the browser asks them for the map areas it shows - never for a photo's
 * position as such, and nothing about the photos themselves. Place names come
 * from the local GeoNames table, so no position is sent anywhere for those.
 */

import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

export const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors · places © <a href="https://www.geonames.org/" target="_blank" rel="noreferrer">GeoNames</a>'

/**
 * OpenStreetMap's tile usage policy asks a web page's tile requests to carry a
 * valid Referer; without one their volunteer-run servers answer with a notice
 * instead of the map. The app's own policy (`Referrer-Policy: same-origin`)
 * sends none off-site, so the tiles - and only the tiles - say which site is
 * asking: the origin alone, never a page path.
 */
export function tiles(): L.TileLayer {
  return L.tileLayer(TILE_URL, {
    attribution: ATTRIBUTION,
    maxZoom: 19,
    referrerPolicy: 'strict-origin-when-cross-origin',
  })
}

/** The design's pin, as a marker. */
export function pinIcon(): L.DivIcon {
  return L.divIcon({
    className: 'map-pin-icon',
    html: '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path d="M12 22s7-6.1 7-12a7 7 0 1 0-14 0c0 5.9 7 12 7 12z" fill="currentColor" stroke="var(--color-surface-raised)" stroke-width="2"/><circle cx="12" cy="10" r="2.6" fill="var(--color-surface-raised)"/></svg>',
    iconSize: [30, 30],
    iconAnchor: [15, 29],
  })
}

/** "south,west,north,east" around these positions, never a zero-size box. */
export function areaAround(positions: readonly (readonly [number, number])[]): string {
  const lats = positions.map((p) => p[0])
  const lons = positions.map((p) => p[1])
  const pad = 0.0005 // about 50 m
  const box = [Math.min(...lats) - pad, Math.min(...lons) - pad, Math.max(...lats) + pad, Math.max(...lons) + pad]
  return box.map((v) => v.toFixed(5)).join(',')
}

export { L }
