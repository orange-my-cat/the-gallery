/**
 * The timeline's justified layout (BRW-01).
 *
 * Items arrive newest first. Each month gets a 64 px header, each day a 40 px
 * header, and each day's photos are packed into rows that fill the usable width
 * at about --size-row-target (210 px) high, preserving every aspect ratio. The
 * last row of a day is never stretched past the target. Geometry matches the
 * design exactly: 24 px left gutter, 72 px right (the scrubber), 4 px gaps.
 */

import { dayLabel, monthLabel, MONTHS, count, parseLocal } from './format'
import type { Asset } from './types'

const PAD_LEFT = 24
const PAD_RIGHT = 72
const GAP = 4
const TARGET = 210
const MONTH_H = 64
const DAY_H = 40
const DAY_AFTER = 14
const TOP = 6

export interface MonthBlock {
  readonly kind: 'month'
  readonly y: number
  readonly h: number
  readonly label: string
  readonly year: number
  readonly count: string
}
export interface DayBlock {
  readonly kind: 'day'
  readonly y: number
  readonly h: number
  readonly key: string
  readonly label: string
  readonly place: string
  readonly count: number
  readonly ids: readonly string[]
}
export interface PlacedTile {
  readonly x: number
  readonly w: number
  readonly asset: Asset
}
export interface RowBlock {
  readonly kind: 'row'
  readonly y: number
  readonly h: number
  readonly tiles: readonly PlacedTile[]
}
export type Block = MonthBlock | DayBlock | RowBlock

export interface TimelineLayout {
  readonly blocks: readonly Block[]
  readonly months: readonly MonthBlock[]
  /** Height of the scroll content. */
  readonly total: number
  /** Where the "still reading" card sits during a first scan. */
  readonly tailY: number
}

const dayKey = (asset: Asset, date: Date) =>
  asset.dateApprox ? `approx:${asset.dateApprox.label}` : date.toDateString()

export function layoutTimeline(items: readonly Asset[], width: number, withTail: boolean): TimelineLayout {
  const usable = Math.max(320, width - PAD_LEFT - PAD_RIGHT)
  const blocks: Block[] = []
  const months: MonthBlock[] = []

  const perMonth = new Map<number, number>()
  const dates = items.map((it) => parseLocal(it.capturedAt))
  dates.forEach((d) => {
    const k = d.getFullYear() * 12 + d.getMonth()
    perMonth.set(k, (perMonth.get(k) ?? 0) + 1)
  })

  let y = TOP
  let currentMonth: number | null = null
  let currentDay: string | null = null
  let day: { asset: Asset; date: Date }[] = []

  const flush = () => {
    const first = day[0]
    if (!first) return
    // Undated files placed by their folder get a group of their own, headed by
    // what is actually known ("On or after 21 Feb 2018"), never by a day.
    const approx = first.asset.dateApprox
    blocks.push({
      kind: 'day', y, h: DAY_H, key: dayKey(first.asset, first.date),
      label: approx ? approx.label : dayLabel(first.date),
      place: approx ? 'No date in the file' : first.asset.place === 'Home' ? '' : first.asset.place,
      count: day.length, ids: day.map((d) => d.asset.id),
    })
    y += DAY_H

    let row: Asset[] = []
    let sum = 0
    const push = (h: number) => {
      let x = PAD_LEFT
      const tiles = row.map((asset) => {
        const w = (asset.width / asset.height) * h
        const tile = { x, w, asset }
        x += w + GAP
        return tile
      })
      blocks.push({ kind: 'row', y, h, tiles })
      y += h + GAP
      row = []
      sum = 0
    }
    for (const { asset } of day) {
      row.push(asset)
      sum += asset.width / asset.height
      if (sum * TARGET + (row.length - 1) * GAP >= usable) push((usable - (row.length - 1) * GAP) / sum)
    }
    if (row.length) push(Math.min(TARGET, (usable - (row.length - 1) * GAP) / sum))
    y += DAY_AFTER
    day = []
  }

  items.forEach((asset, i) => {
    const date = dates[i] as Date
    const mk = date.getFullYear() * 12 + date.getMonth()
    if (mk !== currentMonth) {
      flush()
      const block: MonthBlock = {
        kind: 'month', y, h: MONTH_H, label: monthLabel(date), year: date.getFullYear(),
        count: count(perMonth.get(mk) ?? 0),
      }
      blocks.push(block)
      months.push(block)
      y += MONTH_H
      currentMonth = mk
      currentDay = null
    }
    const dk = dayKey(asset, date)
    if (dk !== currentDay) {
      flush()
      currentDay = dk
    }
    day.push({ asset, date })
  })
  flush()

  const tailY = y + 8
  y += withTail ? 140 : 60
  return { blocks, months, total: y, tailY }
}

/** The month whose header is at or above offset y. */
export function monthAt(layout: TimelineLayout, y: number): MonthBlock | undefined {
  let found = layout.months[0]
  for (const m of layout.months) {
    if (m.y <= y) found = m
    else break
  }
  return found
}

export const shortMonth = (label: string) => {
  const [month = '', year = ''] = label.split(' ')
  return `${MONTHS.find((m) => m === month)?.slice(0, 3) ?? month} ${year}`
}
