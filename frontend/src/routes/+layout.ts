// Backend uses cookie sessions on a different origin (proxied via vite in dev),
// and the SvelteKit server can't see the user without forwarding cookies — so
// just render client-side and let the client check /me.
export const ssr = false;
