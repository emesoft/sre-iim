import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies API + health calls to the FastAPI backend so the frontend
// code always talks to a relative `/api` base (works behind a reverse proxy too).
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two entry points. The sign-in popup loads `auth-callback.html`, which does one thing —
      // hand the auth response back to the window that opened it — and must not pull in the app:
      // booting the SPA inside MSAL's own popup is what produced `block_nested_popups`.
      input: {
        main: 'index.html',
        'auth-callback': 'auth-callback.html',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/healthz': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
