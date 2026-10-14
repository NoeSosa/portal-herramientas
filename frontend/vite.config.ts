import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// El frontend se sirve desde nginx en producción. Durante el desarrollo, Vite
// proxya /api al backend para evitar problemas de CORS.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Los mapas de código no van a producción: facilitan reconocimiento.
    target: "es2022",
  },
});
