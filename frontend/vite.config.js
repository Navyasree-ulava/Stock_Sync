import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://localhost:8000',
      '/inventory': 'http://localhost:8000',
      '/query': 'http://localhost:8000',
      '/history': 'http://localhost:8000',
      '/users': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/tts': 'http://localhost:8000',
    }
  }
})
