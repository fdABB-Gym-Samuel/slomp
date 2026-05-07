import type {
  ArtistSummary,
  GuessResult,
  Room,
  RoomSettings,
  RoomStatus,
  ScoreboardEntry,
  SkipResult,
  SongCandidate,
  SubmittedSong,
  User,
} from "./types";

export class APIError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "APIError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(path, { credentials: "include", ...init, headers });

  if (!res.ok) {
    let code = "unknown";
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (Array.isArray(body?.detail)) {
        // Pydantic validation: array of {loc, msg, type, ...}
        message = body.detail
          .map((e: any) => {
            const loc = Array.isArray(e?.loc)
              ? e.loc.filter((x: unknown) => x !== "body").join(".")
              : "";
            return loc ? `${loc}: ${e.msg}` : e.msg;
          })
          .join("; ");
        code = "validation_error";
      } else if (typeof body?.detail === "string") {
        message = body.detail;
      } else if (body?.detail) {
        code = body.detail.code ?? code;
        message = body.detail.message ?? message;
      }
    } catch {
      // ignore
    }
    throw new APIError(res.status, code, message);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  register: (username: string, password: string) =>
    request<User>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  login: (username: string, password: string) =>
    request<User>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/me"),

  createRoom: () => request<Room>("/rooms", { method: "POST" }),
  getRoom: (code: string) => request<Room>(`/rooms/${code}`),
  joinRoom: (code: string) =>
    request<Room>(`/rooms/${code}/join`, { method: "POST" }),
  leaveRoom: (code: string) =>
    request<void>(`/rooms/${code}/leave`, { method: "POST" }),
  updateSettings: (code: string, settings: RoomSettings) =>
    request<Room>(`/rooms/${code}/settings`, {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  changePhase: (code: string, target: RoomStatus) =>
    request<Room>(`/rooms/${code}/phase`, {
      method: "POST",
      body: JSON.stringify({ target }),
    }),
  restart: (code: string) =>
    request<Room>(`/rooms/${code}/restart`, { method: "POST" }),
  promotePlayer: (code: string, userId: string) =>
    request<Room>(`/rooms/${code}/players/${userId}/promote`, {
      method: "POST",
    }),
  kickPlayer: (code: string, userId: string) =>
    request<void>(`/rooms/${code}/players/${userId}`, { method: "DELETE" }),

  mySongs: (code: string) => request<SubmittedSong[]>(`/rooms/${code}/songs`),
  submitSong: (code: string, spotifyTrackId: string) =>
    request<SubmittedSong>(`/rooms/${code}/songs`, {
      method: "POST",
      body: JSON.stringify({ spotify_track_id: spotifyTrackId }),
    }),
  deleteSong: (code: string, songId: string) =>
    request<void>(`/rooms/${code}/songs/${songId}`, { method: "DELETE" }),

  guess: (code: string, roundId: string, guessedTrackId: string) =>
    request<GuessResult>(`/rooms/${code}/guess`, {
      method: "POST",
      body: JSON.stringify({
        round_id: roundId,
        guessed_track_id: guessedTrackId,
      }),
    }),
  skip: (code: string, roundId: string) =>
    request<SkipResult>(`/rooms/${code}/skip`, {
      method: "POST",
      body: JSON.stringify({ round_id: roundId }),
    }),
  results: (code: string) =>
    request<ScoreboardEntry[]>(`/rooms/${code}/results`),

  spotifySearch: (q: string, roomCode?: string) => {
    const params = new URLSearchParams({ q });
    if (roomCode) params.set("room_code", roomCode);
    return request<SongCandidate[]>(`/spotify/search?${params}`);
  },

  spotifySearchArtists: (q: string) => {
    const params = new URLSearchParams({ q });
    return request<ArtistSummary[]>(`/spotify/artists/search?${params}`);
  },
};
