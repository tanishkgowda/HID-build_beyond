import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// During development Vite serves the kiosk on :5173 and forwards every /api
// request (including the MJPEG preview stream) to A's FastAPI on :8000.
// For the demo, `npm --prefix web run build` writes web/dist, which FastAPI serves at /.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
