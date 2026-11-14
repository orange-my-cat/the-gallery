// Drives the app in Chromium and screenshots each screen (HLD, "Testing and
// operations"). Run through scripts/browser-check.ps1, never directly.
//
// Signed in with a session cookie from `manage.py dev_session`, so no password
// passes through this script. Fails if the page logs an error or a request
// fails that should not.

import { chromium } from 'playwright'

const BASE = process.env.BASE_URL
const SESSION = process.env.SESSION_KEY
const OUT = process.env.OUT_DIR ?? './screenshots'

const problems = []
const browser = await chromium.launch()

function watch(page, label) {
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`[${label}] console: ${m.text()}`)
  })
  page.on('pageerror', (e) => problems.push(`[${label}] page error: ${e.message}`))
  page.on('response', (r) => {
    const url = new URL(r.url())
    // A thumbnail still being made answers 404 by design; the session probe 403s when signed out.
    const expected = (url.pathname.startsWith('/media/thumb/') && r.status() === 404) || url.pathname === '/api/auth/me'
    if (r.status() >= 400 && !expected) problems.push(`[${label}] ${r.status()} ${url.pathname}`)
  })
}

async function shot(page, name) {
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log(`  ${name}.png`)
}

// ── Signed out ───────────────────────────────────────────────────────────────
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  watch(page, 'signin')
  await page.goto(`${BASE}/?mock=off`)
  await page.getByText('Sign in').first().waitFor()
  await shot(page, '01-sign-in')
  await context.close()
}

// ── Signed in, live library ─────────────────────────────────────────────────
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.addCookies([{ name: 'gallery_session', value: SESSION, url: BASE }])
const page = await context.newPage()
watch(page, 'live')

await page.goto(`${BASE}/?mock=off`)
await page.locator('.timeline, .empty-state').first().waitFor({ timeout: 15000 })
await page.waitForLoadState('networkidle')
await shot(page, '02-timeline')

const assets = await page.evaluate(async () => (await (await fetch('/api/assets')).json()).results)
console.log(`  live assets: ${assets.length}`)

const photo = assets.find((a) => a.kind !== 'video')
if (photo) {
  await page.goto(`${BASE}/photo/${photo.id}`)
  await page.locator('.viewer .picture').waitFor()
  await page.waitForLoadState('networkidle')
  await shot(page, '03-viewer-photo')
}

const video = assets.find((a) => a.kind === 'video')
if (video) {
  await page.goto(`${BASE}/photo/${video.id}`)
  await page.locator('.viewer video').waitFor()
  // The bytes must arrive as video whatever the browser can decode.
  const head = await page.evaluate(async (url) => {
    const r = await fetch(url, { headers: { Range: 'bytes=0-1023' } })
    return `${r.status} ${r.headers.get('content-type')}`
  }, video.videoUrl)
  console.log(`  video original: ${head}`)
  if (!head.includes('video/')) problems.push(`[live] video original answered ${head}`)
  // Playwright's Chromium ships without H.264/HEVC decoders (Chrome and Edge
  // have them), so not decoding here is a limit of the check, not a failure.
  const loaded = await page.locator('.viewer video').evaluate(
    (v) =>
      new Promise((resolve) => {
        if (v.readyState >= 1) return resolve(v.duration)
        v.addEventListener('loadedmetadata', () => resolve(v.duration))
        v.addEventListener('error', () => resolve(`error ${v.error?.code}`))
        setTimeout(() => resolve('timeout'), 10000)
      }),
  )
  console.log(`  video metadata in the check's Chromium: ${loaded}`)
  await shot(page, '04-viewer-video')
}

await page.goto(`${BASE}/?q=${encodeURIComponent(process.env.SEARCH ?? 'harbour')}`)
await page.waitForLoadState('networkidle')
await shot(page, '05-search')

// Filters (BRW-06): the panel, then a filter from it shown as a chip.
await page.goto(`${BASE}/`)
await page.waitForLoadState('networkidle')
await page.getByRole('button', { name: 'Filters' }).click()
await page.locator('.filters-panel').waitFor()
await shot(page, '34-filters-panel')
await page.locator('.filters-panel .filter-pill', { hasText: 'Photos' }).click()
await page.locator('.filter-bar .filter-chip').first().waitFor()
await page.waitForLoadState('networkidle')
const chips = await page.locator('.filter-bar .filter-chip').allInnerTexts()
const filtered = await page.locator('.filter-bar .num').innerText()
console.log(`  filter chips: ${JSON.stringify(chips)}; ${filtered}; url ${new URL(page.url()).search}`)
await page.keyboard.press('Escape')
await shot(page, '35-filtered-timeline')
await page.locator('.filter-bar .filter-chip').first().click()
await page.waitForLoadState('networkidle')
await page.locator('.filter-bar').waitFor({ state: 'detached', timeout: 5000 }).catch(() => {})
console.log(`  chip removed: ${(await page.locator('.filter-bar').count()) === 0}`)

// Duplicates (1c): the queue and a group to merge.
await page.goto(`${BASE}/duplicates`)
await page.locator('.folders-head').waitFor()
await page.waitForLoadState('networkidle')
await shot(page, '06-duplicates')
const queue = await page.evaluate(async () => (await (await fetch('/api/duplicates')).json()))
console.log(`  duplicate groups: ${queue.total} (${JSON.stringify(queue.counts)})`)
const mergeable = queue.groups.find((g) => g.kind === 'pixels') ?? queue.groups.find((g) => g.kind === 'exact')
if (mergeable) {
  await page.goto(`${BASE}/duplicates/${mergeable.id}`)
  await page.locator('.copy-card').first().waitFor()
  await page.waitForLoadState('networkidle')
  await shot(page, '17-duplicate-review')
  // Use another copy's value in the first row that has one, as the owner would.
  const cell = page.locator('.mcell:not(.empty):not(.picked)').first()
  if (await cell.count()) {
    await cell.click()
    await page.waitForLoadState('networkidle')
    await page.locator('.mrow').last().scrollIntoViewIfNeeded()
    await shot(page, '18-duplicate-choice')
  }
}

// The map (1d): clusters, a cluster's items, that area in the timeline, and
// the viewer's mini map.
// OpenStreetMap's tile policy: a web page's tile requests must carry a Referer.
const tileReferer = new Promise((resolve) => {
  page.on('request', (r) => {
    if (r.url().includes('tile.openstreetmap.org')) resolve(r.headers()['referer'] ?? '(none)')
  })
})
await page.goto(`${BASE}/map`)
await page.locator('.map-canvas.leaflet-container').waitFor()
console.log(`  OSM tile Referer: ${await Promise.race([tileReferer, new Promise((r) => setTimeout(() => r('(no tile requested)'), 8000))])}`)
await page.waitForLoadState('networkidle')
await page.waitForTimeout(1200) // tiles
await shot(page, '20-map')
const clusters = page.locator('.map-tile')
if (await clusters.count()) {
  await clusters.first().click()
  await page.locator('.map-panel .cell').first().waitFor()
  await page.waitForTimeout(600)
  await shot(page, '21-map-cluster')
  await page.locator('.map-panel .btn-primary').click()
  await page.locator('.folder-bar').waitFor()
  await page.waitForLoadState('networkidle')
  await shot(page, '22-map-area-timeline')
  const placed = await page.evaluate(async () => (await (await fetch('/api/map/points')).json()).points[0][0])
  await page.goto(`${BASE}/photo/${placed}`)
  await page.locator('.mini-map.leaflet-container').waitFor()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1000)
  await shot(page, '23-viewer-mini-map')
} else {
  console.log('  (no geotagged items: the map is empty)')
}

// People (Phase 2): everyone found, one person, their likely faces, and the
// people shown in the viewer.
await page.goto(`${BASE}/people`)
await page.locator('.folders-head').waitFor()
await page.waitForLoadState('networkidle')
await shot(page, '24-people')
if (await page.locator('.same-or-not .son-card').count()) {
  // Skipping changes nothing; it shows the next question.
  const before = await page.locator('.son-face .face-big').getAttribute('style')
  await page.keyboard.press('s')
  await page.locator('.son-last').waitFor()
  const after = await page.locator('.son-face .face-big').getAttribute('style')
  console.log(`  same-person question: skip moved on = ${before !== after}`)
}
const firstPerson = page.locator('.person-card .face-btn').first()
if (await firstPerson.count()) {
  // Selecting people puts their controls in the top bar, pinned while scrolling.
  await page.locator('.person-card').nth(0).hover()
  await page.locator('.person-card .select-dot').nth(0).click()
  await page.locator('.person-card').nth(1).hover()
  await page.locator('.person-card .select-dot').nth(1).click()
  await page.locator('.topbar.selecting').waitFor()
  await page.locator('.folders').evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await shot(page, '28-people-selected-scrolled')
  await page.keyboard.press('Escape')
  await page.locator('.topbar.selecting').waitFor({ state: 'detached' })
  await firstPerson.click()
  await page.locator('.person-head').waitFor()
  await page.waitForLoadState('networkidle')
  // Their photos in the timeline's grid; the viewer opens over the page and
  // steps through only their photos, and closing it returns to them.
  const personPath = new URL(page.url()).pathname
  await page.locator('.tl-scroll .tile').first().waitFor()
  await shot(page, '25-person')
  await page.locator('.tl-scroll .tile').first().click()
  await page.locator('.viewer .picture, .viewer video').first().waitFor()
  const viewerPath = new URL(page.url()).pathname
  await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(300)
  const steppedPath = new URL(page.url()).pathname
  await page.keyboard.press('Escape')
  await page.locator('.viewer').waitFor({ state: 'detached' })
  const closedPath = new URL(page.url()).pathname
  console.log(`  person viewer: ${viewerPath} → ${steppedPath} → closed to ${closedPath}`)
  if (!viewerPath.startsWith(`${personPath}/photo/`) || !steppedPath.startsWith(`${personPath}/photo/`) || closedPath !== personPath) {
    problems.push(`[live] the viewer on ${personPath} did not stay on the person's page`)
  }
  await page.locator('.person-bar .seg-opt', { hasText: 'Faces' }).click()
  await page.locator('.face-tile').first().waitFor()
  await shot(page, '32-person-faces')
  await page.locator('.face-tile .face-img').first().click()
  await page.locator('.topbar.selecting').waitFor()
  await shot(page, '29-person-face-selected')
  await page.keyboard.press('Escape')
  await page.locator('.suggest .pill-btn').click()
  await page.waitForLoadState('networkidle')
  await page.locator('.suggest').scrollIntoViewIfNeeded()
  await shot(page, '26-person-suggestions')
  await page.locator('.face-tile .open').first().click({ force: true })
  await page.locator('.person-chip').first().waitFor()
  await page.waitForTimeout(600)
  await shot(page, '27-viewer-people')
} else {
  console.log('  (no people found yet)')
}

await page.goto(`${BASE}/albums`)
await page.locator('.folders-head').waitFor()
await shot(page, '14-albums')

await page.goto(`${BASE}/trash`)
await page.locator('.folders-head').waitFor()
await shot(page, '15-trash')

await page.goto(`${BASE}/`)
await page.locator('.activity-wrap .icon-btn').click()
await page.locator('.popover.activity').waitFor()
await page.waitForTimeout(600)
await shot(page, '16-activity')
await page.keyboard.press('Escape')

await page.goto(`${BASE}/settings`)
await page.locator('.settings-section').first().waitFor()
await shot(page, '12-settings')

await page.goto(`${BASE}/`)
await page.locator('.scan-wrap [role=button]').click()
await page.locator('.scan-panel .sp-worker').waitFor()
await page.waitForTimeout(800)
await shot(page, '13-scan-panel')
const workerLine = await page.locator('.scan-panel .sp-worker b').textContent()
console.log(`  scan panel says: ${workerLine}`)
await page.keyboard.press('Escape')

// Folders as albums, then one folder's timeline, then its undated group.
await page.goto(`${BASE}/folders`)
await page.locator('.folder-card').first().waitFor()
await page.waitForLoadState('networkidle')
await shot(page, '09-folders')
await page.locator('.folder-card').first().click()
await page.locator('.folder-bar').waitFor()
await page.waitForLoadState('networkidle')
await shot(page, '10-folder-timeline')
const undated = page.locator('.day-head', { hasText: 'No date in the file' }).first()
await page.locator('.tl-scroll').evaluate((el) => el.scrollTo(0, el.scrollHeight))
await page.waitForTimeout(400)
if (await undated.count()) {
  await undated.scrollIntoViewIfNeeded()
  await shot(page, '11-folder-undated-group')
} else {
  console.log('  (no undated group in the first folder)')
}
await context.close()

// ── Demo mode: the design's sample library ──────────────────────────────────
{
  const demo = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await demo.addCookies([{ name: 'gallery_session', value: SESSION, url: BASE }])
  const p = await demo.newPage()
  watch(p, 'demo')
  await p.goto(`${BASE}/?mock=normal`)
  await p.locator('.timeline').waitFor()
  await shot(p, '07-demo-timeline')

  // Minimising a month hides its days and keeps its header; Alt-click folds every month.
  // (Rows are windowed, so check structure rather than how many days are drawn.)
  await p.locator('.month-head').first().click()
  await p.locator('.month-head.collapsed').first().waitFor()
  const foldedOne = await p.locator('.month-head.collapsed + .month-head').count()
  await shot(p, '30-timeline-month-minimised')
  await p.locator('.month-head:not(.collapsed)').first().click({ modifiers: ['Alt'] })
  await p.waitForTimeout(300)
  const allFolded = (await p.locator('.month-head:not(.collapsed)').count()) === 0 && (await p.locator('.day-head').count()) === 0
  await shot(p, '31-timeline-all-minimised')
  await p.locator('.month-head').first().click({ modifiers: ['Alt'] })
  await p.waitForTimeout(300)
  const allOpen = (await p.locator('.month-head.collapsed').count()) === 0 && (await p.locator('.day-head').count()) > 0
  console.log(`  minimise one month: ${foldedOne === 1}, all: ${allFolded}, unfold all: ${allOpen}`)
  if (foldedOne !== 1 || !allFolded || !allOpen) problems.push('[demo] minimising months did not fold and unfold')
  await demo.close()
}

await browser.close()

if (problems.length) {
  console.log('\nProblems:')
  for (const p of problems) console.log(`  ${p}`)
  process.exit(1)
}
console.log('\nNo console errors or failed requests.')
