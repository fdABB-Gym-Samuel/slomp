export interface User {
  id: string;
  username: string;
}

export type RoomStatus = "lobby" | "selecting" | "playing" | "results";

export type HintField = "none" | "artist" | "album";

export interface RoomSettings {
  min_popularity: number;
  required_artists: string[];
  songs_per_player: number;
  guess_brackets_seconds: number[];
  album_art_enabled: boolean;
  album_art_unblur: boolean;
  hint_field: HintField;
  round_intermission_seconds: number;
  round_max_seconds: number;
  post_game_delay_seconds: number;
}

export interface RoomPlayer {
  user: User;
  score: number;
  connected: boolean;
  songs_submitted: number;
}

export interface Room {
  id: string;
  code: string | null;
  name: string | null;
  is_public: boolean;
  leader_id: string;
  status: RoomStatus;
  settings: RoomSettings;
  players: RoomPlayer[];
  current_round_id: string | null;
}

export interface PublicRoom {
  id: string;
  name: string | null;
  leader_username: string;
  player_count: number;
  songs_per_player: number;
}

export interface SongCandidate {
  spotify_track_id: string;
  title: string;
  artist: string;
  album?: string | null;
  preview_url: string | null;
  album_image_url: string | null;
  duration_ms?: number | null;
  popularity?: number | null;
}

export interface SubmittedSong {
  id: string;
  spotify_track_id: string;
  title: string;
  artist: string;
  preview_url: string | null;
  album_image_url: string | null;
}

export interface GuessResult {
  correct: boolean;
  points: number;
  bracket_index: number;
  finished: boolean;
  hint_fulfilled: boolean;
}

export interface SkipResult {
  bracket_index: number;
  finished: boolean;
}

export interface ScoreboardEntry {
  user: User;
  score: number;
  previous_score?: number;
}

export interface ArtistSummary {
  id: string;
  name: string;
  image_url: string | null;
  genres: string[];
  popularity: number | null;
}

export interface ActiveRoundInfo {
  round_id: string;
  picker_ids: string[];
  started_at_server: string;
  audio_url: string;
  album_image_url: string | null;
  guess_brackets_seconds: number[];
  round_max_seconds: number;
}

export interface PickerAttempt {
  user_id: string;
  kind: "guess" | "skip";
  guess_text: string | null;
  correct: boolean | null;
  bracket_index: number;
  hint_fulfilled: boolean;
}
