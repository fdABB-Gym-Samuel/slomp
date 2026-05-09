// Deezer public-API client, called straight from the browser.
//
// Previously the backend proxied every search; with 7+ players in a room
// hammering the search box that bucket emptied fast and Deezer started
// 4xx-ing the whole room. Each client now spends from its own per-IP
// allowance instead.
//
// Deezer's API does not send `Access-Control-Allow-Origin`, so a normal
// `fetch` is blocked by CORS. They support `?output=jsonp&callback=…` —
// inject a <script> tag, the response invokes our callback. The page CSP
// has `https://api.deezer.com` whitelisted in `script-src` for this.

import type { ArtistSummary, RoomSettings, SongCandidate } from "./types";

const API = "https://api.deezer.com";

// Sliding-window limiter sized to roughly match Deezer's documented
// per-IP cap (50 calls / 5 s). One *user search* with required_artists
// can fan out into N+1 calls, so we count individual API calls, not
// user-visible searches.
const RATE_LIMIT_MAX = 50;
const RATE_LIMIT_WINDOW_MS = 5_000;
const recentCalls: number[] = [];

export class RateLimitError extends Error {
  constructor() {
    super("Slow down — 50 searches per 5 s");
    this.name = "RateLimitError";
  }
}

function reserveSlot() {
  const now = Date.now();
  while (recentCalls.length && recentCalls[0] < now - RATE_LIMIT_WINDOW_MS) {
    recentCalls.shift();
  }
  if (recentCalls.length >= RATE_LIMIT_MAX) throw new RateLimitError();
  recentCalls.push(now);
}

let cbCounter = 0;

interface DeezerError {
  error?: { code: number | string; message: string; type?: string };
}

const JSONP_TIMEOUT_MS = 8_000;

function jsonp<T>(
  path: string,
  params: Record<string, string | number> = {},
): Promise<T> {
  reserveSlot();
  return new Promise<T>((resolve, reject) => {
    const cbName = `__deezer_cb_${++cbCounter}`;
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) usp.set(k, String(v));
    usp.set("output", "jsonp");
    usp.set("callback", cbName);

    const script = document.createElement("script");
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const cleanup = () => {
      if (timer != null) clearTimeout(timer);
      delete (window as unknown as Record<string, unknown>)[cbName];
      script.remove();
    };

    (window as unknown as Record<string, (data: T & DeezerError) => void>)[
      cbName
    ] = (data) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (data && typeof data === "object" && "error" in data && data.error) {
        reject(new Error(`Deezer ${data.error.code}: ${data.error.message}`));
        return;
      }
      resolve(data as T);
    };

    script.onerror = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error("Deezer JSONP request failed"));
    };

    // Without a hard timeout the script tag will hang forever if Deezer
    // never responds (e.g. extension blocking the request) — and the
    // caller's UI sits on a "searching…" spinner indefinitely.
    timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error("Deezer request timed out"));
    }, JSONP_TIMEOUT_MS);

    script.src = `${API}${path}?${usp.toString()}`;
    document.head.appendChild(script);
  });
}

interface DeezerTrack {
  id: number;
  title: string;
  rank?: number;
  preview?: string | null;
  duration?: number;
  artist?: { id: number; name: string };
  album?: {
    title?: string;
    cover?: string;
    cover_medium?: string;
    cover_big?: string;
  };
  contributors?: { id: number; name: string }[];
}

interface DeezerArtist {
  id: number;
  name: string;
  picture?: string;
  picture_medium?: string;
}

interface DeezerSearchResponse<T> {
  data: T[];
}

async function searchTracksRaw(
  q: string,
  limit: number,
): Promise<DeezerTrack[]> {
  const data = await jsonp<DeezerSearchResponse<DeezerTrack>>("/search", {
    q,
    limit,
  });
  return data.data ?? [];
}

async function searchArtistsRaw(
  q: string,
  limit: number,
): Promise<DeezerArtist[]> {
  const data = await jsonp<DeezerSearchResponse<DeezerArtist>>(
    "/search/artist",
    { q, limit },
  );
  return data.data ?? [];
}

async function getArtistRaw(id: string): Promise<DeezerArtist> {
  return jsonp<DeezerArtist>(`/artist/${id}`);
}

// Re-rank merged artist-scoped batches: Deezer's relevance gets noisy once
// you concatenate the artist name onto the query, so we score title/token
// overlap ourselves and use rank only as a tiebreaker.
function relevanceScore(track: DeezerTrack, query: string): number {
  const q = query.trim().toLowerCase();
  const title = (track.title ?? "").toLowerCase();
  const artistName = (track.artist?.name ?? "").toLowerCase();
  let score = 0;
  if (q) {
    if (title === q) score += 100;
    else if (title.startsWith(q)) score += 60;
    else if (title.includes(q)) score += 35;

    const qTokens = new Set(q.split(/\s+/).filter(Boolean));
    if (qTokens.size > 0) {
      const tTokens = new Set(title.split(/\s+/).filter(Boolean));
      let overlap = 0;
      for (const t of qTokens) if (tTokens.has(t)) overlap += 1;
      score += (25 * overlap) / qTokens.size;
    }
    if (artistName.includes(q)) score += 5;
  }
  score += (track.rank ?? 0) / 1_000_000;
  return score;
}

function matchesRules(track: DeezerTrack, settings: RoomSettings): boolean {
  if (!track.preview) return false;

  // `rank` is roughly 0..1_000_000+; map min_popularity (0..100) onto it
  // linearly. Backend uses the same factor.
  const rank = track.rank ?? 0;
  if (rank < settings.min_popularity * 10_000) return false;

  const required = settings.required_artists ?? [];
  if (required.length > 0) {
    const ids = new Set<string>();
    if (track.artist?.id != null) ids.add(String(track.artist.id));
    for (const c of track.contributors ?? []) {
      if (c.id != null) ids.add(String(c.id));
    }
    if (!required.some((r) => ids.has(r))) return false;
  }
  return true;
}

function serializeCandidate(track: DeezerTrack): SongCandidate {
  const album = track.album ?? {};
  const image = album.cover_medium ?? album.cover ?? album.cover_big ?? null;
  const rank = track.rank ?? 0;
  const durationSec = track.duration ?? 0;
  return {
    track_id: String(track.id),
    title: track.title ?? "",
    artist: track.artist?.name ?? "",
    album: album.title ?? null,
    preview_url: track.preview ?? null,
    album_image_url: image,
    duration_ms: durationSec * 1000,
    popularity: Math.min(100, Math.floor(rank / 10_000)),
  };
}

function serializeArtist(a: DeezerArtist): ArtistSummary {
  return {
    id: String(a.id),
    name: a.name,
    image_url: a.picture_medium ?? a.picture ?? null,
    genres: [],
    popularity: null,
  };
}

export async function searchSongs(
  query: string,
  settings: RoomSettings | null,
  limit = 10,
): Promise<SongCandidate[]> {
  const required = settings?.required_artists ?? [];

  let tracks: DeezerTrack[];
  if (required.length > 0) {
    // Resolve required IDs → names, then run one search per artist with
    // the name appended free-text (`q=<query> <name>`). We don't use
    // Deezer's `artist:"<name>"` advanced filter because its match is
    // token-fuzzy and lets look-alikes through; matchesRules strips
    // covers/tributes by ID afterwards.
    const artistResults = await Promise.allSettled(
      required.map((id) => getArtistRaw(id)),
    );
    const names = artistResults
      .map((r) => (r.status === "fulfilled" ? r.value.name : null))
      .filter((n): n is string => !!n);

    const batches = await Promise.all(
      names.map(async (name) => {
        const scoped = `${query} ${name}`.trim();
        if (!scoped) return [] as DeezerTrack[];
        try {
          return await searchTracksRaw(scoped, 25);
        } catch {
          return [] as DeezerTrack[];
        }
      }),
    );

    const merged = new Map<number, DeezerTrack>();
    for (const batch of batches) {
      for (const t of batch) {
        if (t.id != null && !merged.has(t.id)) merged.set(t.id, t);
      }
    }
    tracks = [...merged.values()].sort(
      (a, b) => relevanceScore(b, query) - relevanceScore(a, query),
    );
  } else {
    tracks = await searchTracksRaw(query, limit);
  }

  const out: SongCandidate[] = [];
  for (const t of tracks) {
    if (settings && !matchesRules(t, settings)) continue;
    out.push(serializeCandidate(t));
    if (out.length >= limit) break;
  }
  return out;
}

export async function searchArtists(
  query: string,
  limit = 10,
): Promise<ArtistSummary[]> {
  const artists = await searchArtistsRaw(query, limit);
  return artists.map(serializeArtist);
}

export async function getArtists(ids: string[]): Promise<ArtistSummary[]> {
  if (ids.length === 0) return [];
  const results = await Promise.allSettled(ids.map((id) => getArtistRaw(id)));
  return results
    .filter(
      (r): r is PromiseFulfilledResult<DeezerArtist> =>
        r.status === "fulfilled" && r.value?.id != null,
    )
    .map((r) => serializeArtist(r.value));
}
