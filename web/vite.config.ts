import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  plugins: [tailwindcss(), react()],
})
