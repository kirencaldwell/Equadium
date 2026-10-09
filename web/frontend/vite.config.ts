import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    port: 3000,
    proxy: {
      '/games': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/rooms': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/me': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/modes': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
