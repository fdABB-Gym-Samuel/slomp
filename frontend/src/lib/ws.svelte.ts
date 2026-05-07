import { api } from "./api";
import type {
  ActiveRoundInfo,
  PickerAttempt,
  Room,
  ScoreboardEntry,
} from "./types";

interface WSEvent {
  type: string;
  payload: any;
}

class RoomConnection {
  room = $state<Room | null>(null);
  activeRound = $state<ActiveRoundInfo | null>(null);
  bracketIndices = $state<Record<string, number>>({});
  finishedPlayers = $state<
    Record<string, { outcome: "correct" | "exhausted"; points: number }>
  >({});
  pickerAttempts = $state<PickerAttempt[]>([]);
  lastRoundResult = $state<{
    title: string;
    artist: string;
    album_image_url: string | null;
    full_audio_url: string | null;
    scoreboard: ScoreboardEntry[];
    intermission_ends_at: number;
    is_last_round: boolean;
  } | null>(null);
  finalScoreboard = $state<ScoreboardEntry[] | null>(null);

  private ws: WebSocket | null = null;
  private code: string | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private wantConnected = false;

  connect(code: string) {
    if (this.code === code && this.ws) return;
    this.disconnect();
    this.code = code;
    this.wantConnected = true;
    this._open();
  }

  private _open() {
    if (!this.code || !this.wantConnected) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    // In dev (vite on :5173), bypass vite's WS proxy and hit the backend
    // directly on :8000. Vite's WS proxy is unreliable when combined with
    // HMR + overlapping HTTP/WS prefixes. Cookies on localhost are not
    // port-scoped, so the session cookie set via vite-proxied /auth/login
    // is also sent on the direct backend connection. In prod, frontend and
    // backend share an origin via a real reverse proxy.
    const wsHost =
      location.port === "5173" ? `${location.hostname}:8000` : location.host;
    const url = `${proto}//${wsHost}/rooms/${this.code}/ws`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onmessage = (e) => {
      try {
        this._handle(JSON.parse(e.data));
      } catch (err) {
        console.error("ws parse error", err);
      }
    };
    ws.onclose = () => {
      this.ws = null;
      if (this.pingTimer) {
        clearInterval(this.pingTimer);
        this.pingTimer = null;
      }
      if (this.wantConnected) {
        this.reconnectTimer = setTimeout(() => this._open(), 1500);
      }
    };
    ws.onopen = () => {
      this.pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping", payload: {} }));
        }
      }, 25000);
    };
  }

  disconnect() {
    this.wantConnected = false;
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.code = null;
    this.room = null;
    this.activeRound = null;
    this.bracketIndices = {};
    this.finishedPlayers = {};
    this.pickerAttempts = [];
    this.lastRoundResult = null;
    this.finalScoreboard = null;
  }

  private async _refetchRoom() {
    if (!this.code) return;
    try {
      this.room = await api.getRoom(this.code);
    } catch (e) {
      console.error("failed to refetch room", e);
    }
  }

  private _handle(ev: WSEvent) {
    switch (ev.type) {
      case "room_state":
        this.room = ev.payload as Room;
        this._resetRoundState();
        break;

      case "player_joined":
      case "player_left":
      case "player_disconnected":
        this._refetchRoom();
        break;

      case "settings_updated":
        if (this.room) this.room.settings = ev.payload.settings;
        break;

      case "phase_changed":
        if (this.room) {
          this.room.status = ev.payload.status;
          if (ev.payload.status === "lobby") {
            for (const p of this.room.players) {
              p.songs_submitted = 0;
              p.score = 0;
            }
          }
        }
        if (ev.payload.status !== "playing") this._resetRoundState();
        if (ev.payload.status === "lobby") this.finalScoreboard = null;
        break;

      case "song_submitted":
        if (this.room) {
          const p = this.room.players.find(
            (x) => x.user.id === ev.payload.user_id,
          );
          if (p) p.songs_submitted = ev.payload.count;
        }
        break;

      case "round_started":
        this.activeRound = ev.payload as ActiveRoundInfo;
        this.bracketIndices = {};
        this.finishedPlayers = {};
        this.pickerAttempts = [];
        this.lastRoundResult = null;
        break;

      case "bracket_unlocked":
        this.bracketIndices = {
          ...this.bracketIndices,
          [ev.payload.user_id]: ev.payload.bracket_index,
        };
        break;

      case "player_finished":
        this.finishedPlayers = {
          ...this.finishedPlayers,
          [ev.payload.user_id]: {
            outcome: ev.payload.outcome,
            points: ev.payload.points,
          },
        };
        break;

      case "picker_view":
        this.pickerAttempts = [
          ...this.pickerAttempts,
          ...(ev.payload.attempts as PickerAttempt[]),
        ];
        break;

      case "round_ended": {
        const intermissionMs = (ev.payload.intermission_seconds ?? 0) * 1000;
        this.lastRoundResult = {
          title: ev.payload.title,
          artist: ev.payload.artist,
          album_image_url: ev.payload.album_image_url ?? null,
          full_audio_url: ev.payload.full_audio_url ?? null,
          scoreboard: ev.payload.scoreboard,
          intermission_ends_at: Date.now() + intermissionMs,
          is_last_round: !!ev.payload.is_last_round,
        };
        this.activeRound = null;
        if (this.room) {
          for (const sb of ev.payload.scoreboard as ScoreboardEntry[]) {
            const p = this.room.players.find((x) => x.user.id === sb.user.id);
            if (p) p.score = sb.score;
          }
        }
        break;
      }

      case "game_ended":
        this.activeRound = null;
        this.finalScoreboard = ev.payload.scoreboard as ScoreboardEntry[];
        if (this.room) this.room.status = "results";
        break;
    }
  }

  private _resetRoundState() {
    this.activeRound = null;
    this.bracketIndices = {};
    this.finishedPlayers = {};
    this.pickerAttempts = [];
  }
}

export const room = new RoomConnection();
