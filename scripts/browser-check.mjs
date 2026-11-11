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

await page.goto(`${BASE}/duplicates`)
await shot(page, '06-duplicates-not-built')

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
  await p.goto(`${BASE}/duplicates`)
  await p.locator('.review').waitFor()
  await shot(p, '08-demo-duplicates')
  await demo.close()
}

await browser.close()

if (problems.length) {
  console.log('\nProblems:')
  for (const p of problems) console.log(`  ${p}`)
  process.exit(1)
}
console.log('\nNo console errors or failed requests.')
