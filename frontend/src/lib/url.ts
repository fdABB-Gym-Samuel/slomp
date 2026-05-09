// Dev mode runs the SvelteKit server on :5173 and the FastAPI backend on
// :8000. Vite proxies the HTTP routes for us, but its WS proxy is finicky
// when combined with HMR + overlapping HTTP/WS prefixes — so WebSockets
// connect straight to the backend in dev. In prod the two share an origin
// behind a real reverse proxy and `location.host` does the right thing.
//
// Cookies on localhost aren't port-scoped, so the session cookie set via
// the vite-proxied join/create-room call is also sent on the direct
// backend connection.

export function wsBase(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const host =
    location.port === "5173" ? `${location.hostname}:8000` : location.host;
  return `${proto}//${host}`;
}
