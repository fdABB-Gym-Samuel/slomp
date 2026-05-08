<script lang="ts">
  import { api, APIError } from '$lib/api';
  import type { Room, RoomSettings } from '$lib/types';
  import ArtistMultiSelect from './ArtistMultiSelect.svelte';

  let {
    roomData,
    readonly = false,
    dirty = $bindable(false),
  }: {
    roomData: Room;
    readonly?: boolean;
    dirty?: boolean;
  } = $props();

  let local = $state<RoomSettings | null>(null);
  let bracketsText = $state('');
  let saving = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    local = { ...roomData.settings };
    bracketsText = roomData.settings.guess_brackets_seconds.join(', ');
  });

  const isDirty = $derived.by(() => {
    if (!local || readonly) return false;
    const s = roomData.settings;
    if (
      local.songs_per_player !== s.songs_per_player ||
      local.min_popularity !== s.min_popularity ||
      local.album_art_enabled !== s.album_art_enabled ||
      local.album_art_unblur !== s.album_art_unblur ||
      local.hint_field !== s.hint_field ||
      local.round_intermission_seconds !== s.round_intermission_seconds ||
      local.round_max_seconds !== s.round_max_seconds ||
      local.lock_after_lobby !== s.lock_after_lobby
    ) {
      return true;
    }
    if (
      local.required_artists.length !== s.required_artists.length ||
      local.required_artists.some((a, i) => a !== s.required_artists[i])
    ) {
      return true;
    }
    const parsed = parseBrackets(bracketsText);
    if (parsed === null) return true;
    if (
      parsed.length !== s.guess_brackets_seconds.length ||
      parsed.some((b, i) => b !== s.guess_brackets_seconds[i])
    ) {
      return true;
    }
    return false;
  });

  $effect(() => {
    dirty = isDirty;
  });

  function parseBrackets(text: string): number[] | null {
    const parts = text
      .split(/[\s,]+/)
      .filter(Boolean)
      .map((s) => Number(s));
    if (parts.some((n) => !Number.isFinite(n) || n <= 0)) return null;
    for (let i = 1; i < parts.length; i++) {
      if (parts[i] <= parts[i - 1]) return null;
    }
    if (parts.length === 0) return null;
    return parts;
  }

  async function save() {
    if (!local) return;
    const brackets = parseBrackets(bracketsText);
    if (!brackets) {
      error =
        'brackets must be positive numbers in strictly ascending order, e.g. 0.5, 1, 2.5, 5, 15, 30';
      return;
    }
    saving = true;
    error = null;
    try {
      await api.updateSettings(roomData.id, {
        ...local,
        guess_brackets_seconds: brackets,
      });
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      saving = false;
    }
  }
</script>

{#if local}
<div class="card space-y-6">
  <section class="space-y-4">
    <h3 class="text-base font-semibold text-text-primary">Song pool</h3>
    <p class="text-xs text-text-muted">
      Controls which songs each player can submit during the picking phase.
    </p>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="label" for="songs-per-player">Songs per player</label>
        <input
          id="songs-per-player"
          type="number"
          min="1"
          max="10"
          class="input mt-1"
          bind:value={local.songs_per_player}
          disabled={readonly}
        />
      </div>
      <div>
        <label class="label" for="min-popularity">Min popularity (0–100)</label>
        <input
          id="min-popularity"
          type="number"
          min="0"
          max="100"
          class="input mt-1"
          bind:value={local.min_popularity}
          disabled={readonly}
        />
      </div>
    </div>

    <ArtistMultiSelect bind:selected={local.required_artists} {readonly} />
  </section>

  <hr class="border-border" />

  <section class="space-y-4">
    <h3 class="text-base font-semibold text-text-primary">Round mechanics</h3>
    <p class="text-xs text-text-muted">
      How guesses are scored and what info is revealed during a round.
    </p>

    <div>
      <label class="label" for="brackets">Guess brackets (seconds)</label>
      <input
        id="brackets"
        class="input mt-1 font-mono"
        bind:value={bracketsText}
        placeholder="0.5, 1, 2.5, 5, 15, 30"
        disabled={readonly}
      />
      <p class="mt-1 text-xs text-text-muted">
        Strictly ascending. Last value caps the round time.
      </p>
    </div>

    <div class="flex gap-4">
      <label class="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          bind:checked={local.album_art_enabled}
          disabled={readonly}
        />
        Show album art
      </label>
      <label class="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          bind:checked={local.album_art_unblur}
          disabled={readonly || !local.album_art_enabled}
        />
        Gradual unblur
      </label>
    </div>

    <div>
      <label class="label" for="hint-field">Hint field</label>
      <select
        id="hint-field"
        class="input mt-1"
        bind:value={local.hint_field}
        disabled={readonly}
      >
        <option value="none">None</option>
        <option value="artist">Artist</option>
        <option value="album">Album</option>
      </select>
      <p class="mt-1 text-xs text-text-muted">
        Wrong guesses that share this field with the correct song are flagged
        as a hint (shown in warning colour) but still advance the bracket.
      </p>
    </div>
  </section>

  <hr class="border-border" />

  <section class="space-y-4">
    <h3 class="text-base font-semibold text-text-primary">Pacing</h3>
    <p class="text-xs text-text-muted">
      How long rounds run and how much breathing room there is between them.
    </p>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="label" for="round-intermission">
          Round intermission (seconds)
        </label>
        <input
          id="round-intermission"
          type="number"
          min="0"
          max="30"
          class="input mt-1"
          bind:value={local.round_intermission_seconds}
          disabled={readonly}
        />
        <p class="mt-1 text-xs text-text-muted">
          Pause between rounds to reveal the song and show leaderboard movement.
        </p>
      </div>
      <div>
        <label class="label" for="round-max">Round time limit (seconds)</label>
        <input
          id="round-max"
          type="number"
          min="10"
          max="600"
          class="input mt-1"
          bind:value={local.round_max_seconds}
          disabled={readonly}
        />
        <p class="mt-1 text-xs text-text-muted">
          When this elapses, anyone still guessing is auto-exhausted with 0 pts.
        </p>
      </div>
    </div>
  </section>

  <hr class="border-border" />

  <section class="space-y-4">
    <h3 class="text-base font-semibold text-text-primary">Access</h3>
    <p class="text-xs text-text-muted">
      What happens to people who try to join once the game has started.
    </p>

    <label class="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        class="mt-1"
        bind:checked={local.lock_after_lobby}
        disabled={readonly}
      />
      <span>
        Late joiners spectate
        <span class="block text-xs text-text-muted">
          Once the game leaves the lobby, anyone joining is added as a
          spectator — they can watch but won't pick songs, guess, or score
          until the current game finishes and the room returns to the lobby.
          Without this, late joiners slot into the next round of the current
          game.
        </span>
      </span>
    </label>
  </section>

  {#if !readonly}
    {#if error}
      <p class="text-sm text-danger">{error}</p>
    {/if}

    <button class="btn-primary w-full" disabled={saving} onclick={save}>
      {saving ? 'Saving…' : 'Save settings'}
    </button>
  {/if}
</div>
{/if}
