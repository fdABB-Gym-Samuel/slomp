import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      pages: "build",
      assets: "build",
      fallback: "index.html",
      precompress: false,
      strict: false,
    }),
    csp: {
      mode: "hash",
      directives: {
        "default-src": ["self"],
        // api.deezer.com is whitelisted so the JSONP <script> tags from
        // src/lib/deezer.ts can load. Deezer's REST endpoints don't send
        // CORS headers, so JSONP is the only browser-direct option.
        "script-src": ["self", "https://api.deezer.com"],
        // Tailwind's compiled CSS is served from 'self'; 'unsafe-inline'
        // is needed for dynamic style="…" attrs on Svelte components
        // (timer color, progress bars, marker positions in PlayingPhase).
        "style-src": ["self", "unsafe-inline"],
        // Album covers and artist avatars come from Deezer's image CDN.
        // data: covers favicons / inlined SVG.
        "img-src": ["self", "data:", "https://*.dzcdn.net"],
        // Round audio is sliced and served by our backend ('self').
        // Track previews in SelectingPhase load straight from Deezer's
        // preview CDN.
        "media-src": ["self", "https://*.dzcdn.net"],
        // WebSocket schemes are allow-listed broadly so dev mode (where
        // the SvelteKit server on :5173 connects to the backend on :8000)
        // works; in prod 'self' covers the same-origin wss connection.
        "connect-src": ["self", "ws:", "wss:"],
        "font-src": ["self", "data:"],
        "object-src": ["none"],
        "base-uri": ["self"],
        "frame-ancestors": ["none"],
        "form-action": ["self"],
      },
    },
  },
};
