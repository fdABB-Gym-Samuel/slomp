import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/me": "http://localhost:8000",
      "/spotify": "http://localhost:8000",
      "/health": "http://localhost:8000",
      // Note: only HTTP traffic for /rooms is proxied here. The WebSocket
      // (/rooms/{code}/ws) bypasses vite and connects directly to the
      // backend on :8000 in dev — see $lib/ws.svelte.ts. Vite's WS proxy is
      // notoriously finicky when combined with HMR + overlapping HTTP/WS
      // prefixes, and a cookie scoped to "localhost" (no port) covers both
      // origins anyway.
      "/rooms": "http://localhost:8000",
    },
  },
});
