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
        // api.deezer.com is whitelisted so the JSONP <script> tags from
        // src/lib/deezer.ts can load. Deezer's REST endpoints don't send
        // CORS headers, so JSONP is the only browser-direct option.
        "script-src": ["self", "https://api.deezer.com"],
      },
    },
  },
};
