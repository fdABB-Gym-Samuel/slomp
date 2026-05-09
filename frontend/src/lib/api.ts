import type {
  GuessResult,
  PublicRoom,
  Room,
  RoomSettings,
  RoomStatus,
  ScoreboardEntry,
  SkipResult,
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
  me: () => request<User>("/me"),

  createRoom: (username: string) =>
    request<Room>("/rooms", {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  getRoom: (id: string) => request<Room>(`/rooms/${id}`),
  joinRoom: (id: string, username: string) =>
    request<Room>(`/rooms/${id}/join`, {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  joinByCode: (code: string, username: string) =>
    request<Room>(`/rooms/join-by-code`, {
      method: "POST",
      body: JSON.stringify({ code, username }),
    }),
  leaveRoom: (id: string) =>
    request<void>(`/rooms/${id}/leave`, { method: "POST" }),
  renameInRoom: (id: string, username: string) =>
    request<User>(`/rooms/${id}/me/username`, {
      method: "PATCH",
      body: JSON.stringify({ username }),
    }),
  updateSettings: (id: string, settings: RoomSettings) =>
    request<Room>(`/rooms/${id}/settings`, {
      method: "PATCH",
      body: JSON.stringify(settings),
    }),
  updateRoomInfo: (
    id: string,
    info: { name?: string | null; is_public?: boolean },
  ) =>
    request<Room>(`/rooms/${id}`, {
      method: "PATCH",
      body: JSON.stringify(info),
    }),
  listPublicRooms: () => request<PublicRoom[]>("/rooms/public"),
  changePhase: (id: string, target: RoomStatus) =>
    request<Room>(`/rooms/${id}/phase`, {
      method: "POST",
      body: JSON.stringify({ target }),
    }),
  restart: (id: string) =>
    request<Room>(`/rooms/${id}/restart`, { method: "POST" }),
  promotePlayer: (id: string, userId: string) =>
    request<Room>(`/rooms/${id}/players/${userId}/promote`, {
      method: "POST",
    }),
  kickPlayer: (id: string, userId: string) =>
    request<void>(`/rooms/${id}/players/${userId}`, { method: "DELETE" }),

  mySongs: (id: string) => request<SubmittedSong[]>(`/rooms/${id}/songs`),
  submitSong: (id: string, spotifyTrackId: string) =>
    request<SubmittedSong>(`/rooms/${id}/songs`, {
      method: "POST",
      body: JSON.stringify({ spotify_track_id: spotifyTrackId }),
    }),
  deleteSong: (id: string, songId: string) =>
    request<void>(`/rooms/${id}/songs/${songId}`, { method: "DELETE" }),

  guess: (id: string, roundId: string, guessedTrackId: string) =>
    request<GuessResult>(`/rooms/${id}/guess`, {
      method: "POST",
      body: JSON.stringify({
        round_id: roundId,
        guessed_track_id: guessedTrackId,
      }),
    }),
  skip: (id: string, roundId: string) =>
    request<SkipResult>(`/rooms/${id}/skip`, {
      method: "POST",
      body: JSON.stringify({ round_id: roundId }),
    }),
  results: (id: string) => request<ScoreboardEntry[]>(`/rooms/${id}/results`),
};
