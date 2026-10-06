import { defineConfig } from "vite";

// `npm run dev` serves the UI on :5173 and forwards API/WebSocket calls to `hq serve` on :8750.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8750",
      "/avatars": "http://127.0.0.1:8750",
      "/ws": { target: "ws://127.0.0.1:8750", ws: true },
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 2000 },
});
