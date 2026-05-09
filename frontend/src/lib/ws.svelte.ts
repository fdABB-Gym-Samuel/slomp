import { auth } from "./auth.svelte";
import { wsBase } from "./url";
import type {
  ActiveRoundInfo,
  MyAttempt,
  PickerAttempt,
  Room,
  RoomPlayer,
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
  myAttempts = $state<MyAttempt[]>([]);
  kickedSelf = $state(false);
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
  private roomId: string | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private wantConnected = false;

  connect(roomId: string) {
    if (this.roomId === roomId && this.ws) return;
    this.disconnect();
    this.roomId = roomId;
    this.wantConnected = true;
    this._open();
  }

  private _open() {
    if (!this.roomId || !this.wantConnected) return;
    const ws = new WebSocket(`${wsBase()}/rooms/${this.roomId}/ws`);
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
    this.roomId = null;
    this.room = null;
    this.activeRound = null;
    this.bracketIndices = {};
    this.finishedPlayers = {};
    this.pickerAttempts = [];
    this.myAttempts = [];
    this.kickedSelf = false;
    this.lastRoundResult = null;
    this.finalScoreboard = null;
  }

  private _handle(ev: WSEvent) {
    switch (ev.type) {
      case "room_state":
        this.room = ev.payload as Room;
        this._resetRoundState();
        break;

      case "player_joined":
        if (this.room) {
          const existing = this.room.players.find(
            (x) => x.user.id === ev.payload.user_id,
          );
          if (existing) {
            existing.connected = true;
            existing.auto_leave_at = null;
          } else if (ev.payload.player) {
            // Server now ships the full player record so we can upsert
            // without a refetch (which could race subsequent WS events).
            this.room.players = [
              ...this.room.players,
              ev.payload.player as RoomPlayer,
            ];
          }
        }
        break;

      case "player_disconnected":
        if (this.room) {
          const p = this.room.players.find(
            (x) => x.user.id === ev.payload.user_id,
          );
          if (p) {
            p.connected = false;
            p.auto_leave_at = ev.payload.auto_leave_at ?? null;
          }
        }
        break;

      case "player_left":
        if (this.room) {
          this.room.players = this.room.players.filter(
            (p) => p.user.id !== ev.payload.user_id,
          );
        }
        break;

      case "player_kicked":
        if (auth.user && ev.payload.user_id === auth.user.id) {
          this.disconnect();
          this.kickedSelf = true;
        } else if (this.room) {
          this.room.players = this.room.players.filter(
            (p) => p.user.id !== ev.payload.user_id,
          );
        }
        break;

      case "leader_changed":
        if (this.room) this.room.leader_id = ev.payload.leader_id;
        break;

      case "player_renamed":
        if (this.room) {
          const p = this.room.players.find(
            (x) => x.user.id === ev.payload.user_id,
          );
          if (p) p.user.username = ev.payload.username;
        }
        if (auth.user && ev.payload.user_id === auth.user.id) {
          auth.setUser({ ...auth.user, username: ev.payload.username });
        }
        break;

      case "settings_updated":
        if (this.room) this.room.settings = ev.payload.settings;
        break;

      case "room_info_updated":
        if (this.room) {
          this.room.name = ev.payload.name ?? null;
          this.room.is_public = !!ev.payload.is_public;
          this.room.code = ev.payload.code ?? null;
        }
        break;

      case "phase_changed":
        if (this.room) {
          this.room.status = ev.payload.status;
          if (ev.payload.status === "lobby") {
            // Server promotes spectators to full members on the way back
            // to the lobby; mirror that locally so the badges drop.
            for (const p of this.room.players) {
              p.songs_submitted = 0;
              p.score = 0;
              p.spectating = false;
            }
          }
        }
        if (ev.payload.status !== "playing") this._resetRoundState();
        if (ev.payload.status === "lobby") this.finalScoreboard = null;
        break;

      case "song_submitted":
      case "song_removed":
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
        this.myAttempts = [];
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

      case "my_attempts":
        // Sent by the server on reconnect to restore the current player's
        // own guess/skip history for the active round.
        this.myAttempts = (ev.payload.attempts as PickerAttempt[]).map((a) => ({
          kind: a.kind,
          bracket_index: a.bracket_index,
          guess_text: a.guess_text,
          correct: a.correct,
          hint_fulfilled: a.hint_fulfilled,
        }));
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
    this.myAttempts = [];
  }
}

export const room = new RoomConnection();
