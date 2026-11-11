import { resolve } from 'node:path'
import process from 'node:process'

import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// Configuration is read from the repository-root .env, the same file Django
// reads, so the ports are stated once (HLD, Deployment and environments).
const repoRoot = resolve(process.cwd(), '..')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, '')

  const djangoPort = env.DJANGO_DEV_PORT ?? '8001'
  // The browser check (scripts/browser-check.ps1) runs a second, short-lived
  // instance that Chromium in a container can reach: 0.0.0.0 on 5174, and the
  // `host.docker.internal` Host header. Set only for that process.
  const check = process.env.VITE_CHECK === '1'
  const vitePort = check ? 5174 : Number(env.VITE_DEV_PORT ?? '5173')
  const django = { target: `http://localhost:${djangoPort}`, changeOrigin: false }

  return {
    plugins: [react()],

    // In production the bundle is served by WhiteNoise from /static/.
    base: mode === 'production' ? '/static/' : '/',

    resolve: {
      alias: { '@': resolve(process.cwd(), 'src') },
    },

    server: {
      port: vitePort,
      ...(check ? { host: '0.0.0.0', allowedHosts: ['host.docker.internal'] } : {}),
      // Fail rather than silently move: the port is written into
      // CSRF_TRUSTED_ORIGINS, and a silent move breaks sign-in invisibly.
      strictPort: true,
      // One origin for the browser, so the session cookie behaves exactly as
      // it will behind nginx in production. No CORS layer.
      proxy: {
        '/api': django,
        '/media': django,
      },
    },

    build: {
      outDir: 'dist',
      emptyOutDir: true,
      sourcemap: false,
    },
  }
})
