/**
 * Sample library, ported from the design prototype's generator so the screens
 * look as approved until the indexer (Phase 1a) produces real thumbnails.
 * Deterministic: the same seed always yields the same 2,200 items.
 * Photos are painted as CSS gradients ("thumb") in place of /media/thumb URLs.
 */

import { pad, toLocalString } from '../format'
import type { Asset, MediaKind } from '../types'

function rand(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
type Rng = ReturnType<typeof rand>
const pick = <T,>(r: Rng, list: readonly T[]): T => list[Math.floor(r() * list.length)] as T

interface Scene {
  tag: string
  sky: [string, string]
  mid: string
  g: [string, string]
  sun?: string
  glow?: string
}

const SCENES: Scene[] = [
  { tag: 'sunset', sky: ['oklch(0.80 0.08 70)', 'oklch(0.64 0.13 38)'], mid: 'oklch(0.42 0.06 30)', g: ['oklch(0.30 0.04 35)', 'oklch(0.19 0.03 30)'], sun: 'oklch(0.95 0.07 90)', glow: 'oklch(0.9 0.12 75 / 0.55)' },
  { tag: 'mountains', sky: ['oklch(0.88 0.035 235)', 'oklch(0.72 0.08 240)'], mid: 'oklch(0.6 0.035 250)', g: ['oklch(0.5 0.06 150)', 'oklch(0.35 0.05 150)'] },
  { tag: 'coast', sky: ['oklch(0.91 0.025 220)', 'oklch(0.8 0.05 215)'], mid: 'oklch(0.56 0.08 225)', g: ['oklch(0.5 0.08 220)', 'oklch(0.33 0.07 232)'], sun: 'oklch(0.98 0.02 95)', glow: 'oklch(0.97 0.03 90 / 0.4)' },
  { tag: 'desert', sky: ['oklch(0.9 0.04 85)', 'oklch(0.82 0.06 70)'], mid: 'oklch(0.66 0.1 48)', g: ['oklch(0.7 0.09 65)', 'oklch(0.54 0.1 50)'], sun: 'oklch(0.99 0.03 95)', glow: 'oklch(0.97 0.06 90 / 0.5)' },
  { tag: 'fog', sky: ['oklch(0.86 0.008 100)', 'oklch(0.77 0.012 100)'], mid: 'oklch(0.67 0.015 130)', g: ['oklch(0.57 0.02 130)', 'oklch(0.44 0.02 130)'] },
  { tag: 'night', sky: ['oklch(0.33 0.06 270)', 'oklch(0.22 0.05 265)'], mid: 'oklch(0.21 0.03 260)', g: ['oklch(0.15 0.02 260)', 'oklch(0.1 0.01 260)'], sun: 'oklch(0.94 0.03 95)', glow: 'oklch(0.7 0.06 260 / 0.35)' },
  { tag: 'forest', sky: ['oklch(0.89 0.03 180)', 'oklch(0.78 0.05 170)'], mid: 'oklch(0.46 0.06 155)', g: ['oklch(0.36 0.06 150)', 'oklch(0.23 0.04 150)'] },
  { tag: 'fields', sky: ['oklch(0.86 0.05 230)', 'oklch(0.75 0.07 235)'], mid: 'oklch(0.56 0.1 60)', g: ['oklch(0.64 0.12 78)', 'oklch(0.47 0.1 60)'], sun: 'oklch(0.98 0.03 95)', glow: 'oklch(0.98 0.04 95 / 0.35)' },
]
const OBJ_BG: [string, string][] = [
  ['oklch(0.87 0.025 80)', 'oklch(0.77 0.035 70)'], ['oklch(0.82 0.03 200)', 'oklch(0.7 0.03 210)'],
  ['oklch(0.35 0.02 60)', 'oklch(0.25 0.02 50)'], ['oklch(0.9 0.03 30)', 'oklch(0.8 0.05 35)'],
]
const OBJ_FG = ['oklch(0.62 0.13 45)', 'oklch(0.56 0.08 150)', 'oklch(0.76 0.12 88)', 'oklch(0.46 0.07 250)', 'oklch(0.92 0.015 90)', 'oklch(0.3 0.03 40)']

function landscape(r: Rng, sc: Scene): string {
  const hz = 48 + r() * 24
  const layers: string[] = []
  if (sc.sun && sc.glow && r() < 0.75) {
    const sx = (12 + r() * 76).toFixed(1)
    const sy = Math.max(10, hz - 6 - r() * 30).toFixed(1)
    layers.push(`radial-gradient(circle at ${sx}% ${sy}%, ${sc.sun} 0 3.2%, transparent 3.6%)`, `radial-gradient(circle at ${sx}% ${sy}%, ${sc.glow} 0, transparent 38%)`)
  }
  layers.push(`linear-gradient(${(180 + (r() - 0.5) * 10).toFixed(1)}deg, transparent ${hz.toFixed(1)}%, ${sc.g[0]} ${hz.toFixed(1)}%, ${sc.g[1]})`)
  if (r() < 0.7) {
    const m = hz - 3 - r() * 9
    layers.push(`linear-gradient(${(180 + (r() - 0.5) * 34).toFixed(1)}deg, transparent ${m.toFixed(1)}%, ${sc.mid} ${m.toFixed(1)}%)`)
  }
  layers.push(`linear-gradient(${sc.sky[0]}, ${sc.sky[1]} ${hz.toFixed(1)}%)`)
  return layers.join(', ')
}

function still(r: Rng): string {
  const b = pick(r, OBJ_BG), f = pick(r, OBJ_FG)
  const x = 30 + r() * 40, y = 40 + r() * 16, s = 11 + r() * 10, tb = 60 + r() * 14
  return `radial-gradient(circle at ${x.toFixed(1)}% ${y.toFixed(1)}%, ${f} 0 ${s.toFixed(1)}%, transparent ${(s + 0.5).toFixed(1)}%), radial-gradient(ellipse 30% 6% at ${x.toFixed(1)}% ${Math.min(94, y + s * 1.25).toFixed(1)}%, rgb(0 0 0 / 0.25), transparent), linear-gradient(transparent ${tb.toFixed(1)}%, rgb(0 0 0 / 0.08) ${tb.toFixed(1)}%), linear-gradient(160deg, ${b[0]}, ${b[1]})`
}

const PLACES: { n: string; gps: [number, number] | null; sc: number[] }[] = [
  { n: 'Point Addis, Victoria', gps: [-38.392, 144.252], sc: [2, 0] },
  { n: 'Grampians, Victoria', gps: [-37.15, 142.45], sc: [1, 7] },
  { n: 'Lake Tekapo, New Zealand', gps: [-44.004, 170.477], sc: [1, 5] },
  { n: 'Hobart, Tasmania', gps: [-42.882, 147.327], sc: [4, 2] },
  { n: 'Kangaroo Valley, NSW', gps: [-34.737, 150.53], sc: [6, 4] },
  { n: 'Margaret River, WA', gps: [-33.955, 115.075], sc: [2, 3] },
  { n: 'Flinders Ranges, SA', gps: [-31.5, 138.6], sc: [3, 0] },
  { n: 'Home', gps: null, sc: [-1] },
]
const CAMS: { name: string; lenses: [string, number][] }[] = [
  { name: 'Fujifilm X-T4', lenses: [['XF 23mm F2 R WR', 23], ['XF 16-80mm F4 R OIS WR', 35], ['XF 56mm F1.2 R', 56]] },
  { name: 'Google Pixel 8', lenses: [['Main camera', 6.9]] },
  { name: 'Apple iPhone 13 Pro', lenses: [['Main camera', 5.7], ['Ultra wide camera', 1.57]] },
]
const SHUTTER = ['1/30', '1/60', '1/125', '1/250', '1/500', '1/1000', '1/2000']

export const LIBRARY_PATH = 'D:\\Photos'
export const LIBRARY_SIZE = 2200

export function buildLibrary(): Asset[] {
  const r = rand(20261108)
  const out: Asset[] = []
  const d = new Date(2026, 10, 7)
  let seq = 4100
  let iseq = 200
  while (out.length < LIBRARY_SIZE) {
    const room = LIBRARY_SIZE - out.length
    const n = Math.min(room, r() < 0.12 ? 12 + Math.floor(r() * 18) : 2 + Math.floor(r() * 9))
    const pl = pick(r, PLACES)
    const scI = (r() < 0.75 ? pl.sc[0] : pl.sc[pl.sc.length - 1]) as number
    let t = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 19, Math.floor(r() * 50), Math.floor(r() * 60))
    for (let i = 0; i < n; i++) {
      t = new Date(t.getTime() - (3 + r() * 26) * 60000)
      const ci = r() < 0.45 ? 0 : r() < 0.55 ? 1 : 2
      const cam = CAMS[ci] as (typeof CAMS)[number]
      const lens = pick(r, cam.lenses)
      const portrait = r() < 0.28
      let kind: MediaKind = 'jpg'
      let w: number, h: number, ext: string, name: string
      let pair: string | null = null
      let size: number
      let pairSize = 0
      let len = 0
      const stamp = `${t.getFullYear()}${pad(t.getMonth() + 1)}${pad(t.getDate())}_${pad(t.getHours())}${pad(t.getMinutes())}${pad(t.getSeconds())}`
      if (ci === 0) {
        w = 6240; h = 4160
        const base = 'DSCF' + pad(seq++ % 10000, 4)
        const k = r()
        if (k < 0.35) { kind = 'pair'; ext = 'RAF'; name = base + '.RAF'; pair = base + '.JPG'; size = (28 + r() * 6) * 1e6; pairSize = (9 + r() * 5) * 1e6 }
        else if (k < 0.55) { kind = 'raw'; ext = 'RAF'; name = base + '.RAF'; size = (28 + r() * 6) * 1e6 }
        else { ext = 'JPG'; name = base + '.JPG'; size = (9 + r() * 5) * 1e6 }
      } else if (ci === 1) {
        if (r() < 0.3) { kind = 'video'; w = 1920; h = 1080; ext = 'MP4'; name = 'PXL_' + stamp + '.mp4'; len = 3 + Math.floor(r() * r() * 150); size = len * 1.9e6 }
        else { w = 4080; h = 3072; ext = 'JPG'; name = 'PXL_' + stamp + '.jpg'; size = (2.5 + r() * 3.5) * 1e6 }
      } else if (r() < 0.25) {
        kind = 'video'; w = 1920; h = 1080; ext = 'MOV'; name = 'IMG_' + pad(iseq++ % 10000, 4) + '.MOV'; len = 3 + Math.floor(r() * r() * 120); size = len * 2.1e6
      } else {
        kind = 'heic'; w = 4032; h = 3024; ext = 'HEIC'; name = 'IMG_' + pad(iseq++ % 10000, 4) + '.HEIC'; size = (1.8 + r() * 2.7) * 1e6
      }
      if (portrait) [w, h] = [h, w]
      const sc = scI < 0 ? null : (SCENES[(scI + (r() < 0.2 ? 1 : 0)) % SCENES.length] as Scene)
      const thumb = sc ? landscape(r, sc) : still(r)
      const tags = sc ? [sc.tag] : ['still life']
      if (r() < 0.3) tags.push(pick(r, ['family', 'walk', 'golden hour', 'wide', 'detail', 'weekend']))
      const Y = t.getFullYear(), M = pad(t.getMonth() + 1), D = pad(t.getDate())
      const folder = `${LIBRARY_PATH}\\${Y}\\${Y}-${M}-${D}${pl.gps ? ' ' + (pl.n.split(',')[0] ?? '') : ''}\\`
      const fNumber = ci === 0 ? pick(r, [1.4, 2, 2.8, 4, 5.6, 8, 11]) : ci === 1 ? 1.7 : 1.5
      const shutter = pick(r, SHUTTER)
      const iso = pick(r, ci === 0 ? [160, 200, 400, 800, 1600, 3200] : [50, 100, 200, 400, 800])
      const gps: [number, number] | null = pl.gps ? [pl.gps[0] + (r() - 0.5) * 0.02, pl.gps[1] + (r() - 0.5) * 0.02] : null
      const favourite = r() < 0.06
      const rating = r() < 0.2 ? 3 + Math.floor(r() * 3) : 0
      out.push({
        id: 'p' + out.length, capturedAt: toLocalString(t), captureOffset: '+10:00', kind, ext, name, pairName: pair,
        width: w, height: h, durationS: len, size, pairSize, camera: cam.name, lens: lens[0], focalLength: lens[1],
        fNumber, shutter, iso, place: pl.n, gps, folder, title: '', description: '', dateEdited: false,
        favourite, rating, tags,
        albums: pl.n.startsWith('Point') && Y === 2024 ? ['Coast 2024'] : pl.n.startsWith('Hobart') ? ['Tasmania'] : [],
        thumb, preview: thumb, originalUrl: '#', videoUrl: null, liveUrl: null, missing: false, dateApprox: null,
      })
    }
    d.setDate(d.getDate() - (1 + Math.floor(r() * r() * 14)))
  }
  return out
}

export const SAMPLE_ALBUMS: [string, number][] = [['Coast 2024', 214], ['Tasmania', 88], ['Still life', 41], ['To print', 12]]
export const SAMPLE_FOLDERS = ['D:\\Photos\\Archive', 'D:\\Photos\\To sort', 'D:\\Photos\\2026']
export const SAMPLE_TAGS = ['coast', 'sunset', 'mountains', 'family', 'to edit']
