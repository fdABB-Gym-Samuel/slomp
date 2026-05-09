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
  let blursText = $state('');
  let saving = $state(false);
  let error = $state<string | null>(null);

  // Sync local from `roomData.settings` only when the server-side values
  // actually change. Without this, an unrelated room refetch (e.g. on
  // `player_left`, which produces a new `room` object with structurally
  // identical settings) would clobber the leader's unsaved edits.
  let lastServerSnapshot = '';
  $effect(() => {
    const snap = JSON.stringify(roomData.settings);
    if (snap === lastServerSnapshot) return;
    lastServerSnapshot = snap;
    local = { ...roomData.settings };
    bracketsText = roomData.settings.guess_brackets_seconds.join(', ');
    blursText = (
      roomData.settings.album_art_obscure_per_bracket_px ?? []
    ).join(', ');
  });

  const isDirty = $derived.by(() => {
    if (!local || readonly) return false;
    const s = roomData.settings;
    if (
      local.game_mode !== s.game_mode ||
      local.random_song_count !== s.random_song_count ||
      local.songs_per_player !== s.songs_per_player ||
      local.min_popularity !== s.min_popularity ||
      local.album_art_enabled !== s.album_art_enabled ||
      local.album_art_unblur !== s.album_art_unblur ||
      local.album_art_obscure_mode !== s.album_art_obscure_mode ||
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
    const parsedBlurs = parseBlurs(blursText);
    if (parsedBlurs === null) return true;
    const currentBlurs = s.album_art_obscure_per_bracket_px ?? [];
    if (
      parsedBlurs.length !== currentBlurs.length ||
      parsedBlurs.some((b, i) => b !== currentBlurs[i])
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

  // Empty input means "no override" — return [] (valid). Otherwise every
  // entry must be a finite number in 0..256; length-vs-brackets is checked
  // in save() since it depends on the parsed brackets.
  function parseBlurs(text: string): number[] | null {
    const trimmed = text.trim();
    if (!trimmed) return [];
    const parts = trimmed
      .split(/[\s,]+/)
      .filter(Boolean)
      .map((s) => Number(s));
    if (parts.some((n) => !Number.isFinite(n) || n < 0 || n > 256)) return null;
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
    const blurs = parseBlurs(blursText);
    if (blurs === null) {
      error =
        local.album_art_obscure_mode === 'pixelate'
          ? 'pixelation block sizes must be numbers between 1 and 256'
          : 'blur values must be numbers between 0 and 256';
      return;
    }
    if (blurs.length > 0 && blurs.length !== brackets.length) {
      error = `obscure values must have one entry per bracket (got ${blurs.length}, expected ${brackets.length}) — leave empty for the default`;
      return;
    }
    saving = true;
    error = null;
    try {
      await api.updateSettings(roomData.id, {
        ...local,
        guess_brackets_seconds: brackets,
        album_art_obscure_per_bracket_px: blurs,
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
    <h3 class="text-base font-semibold text-text-primary">Game mode</h3>
    <p class="text-xs text-text-muted">
      Classic = each player submits their own picks during a selection phase.
      Random = the server fills the queue with popular tracks (filtered by
      min popularity) and everyone guesses every song.
    </p>

    <div>
      <label class="label" for="game-mode">Mode</label>
      <select
        id="game-mode"
        class="input mt-1"
        bind:value={local.game_mode}
        disabled={readonly}
      >
        <option value="classic">Classic — players pick</option>
        <option value="random">Random — auto-pick popular tracks</option>
      </select>
    </div>
  </section>

  <hr class="border-border" />

  <section class="space-y-4">
    <h3 class="text-base font-semibold text-text-primary">Song pool</h3>
    <p class="text-xs text-text-muted">
      {#if local.game_mode === 'random'}
        Min popularity filters the random pool. Required artists only apply in
        classic mode.
      {:else}
        Controls which songs each player can submit during the picking phase.
      {/if}
    </p>

    <div class="grid grid-cols-2 gap-4">
      {#if local.game_mode === 'random'}
        <div>
          <label class="label" for="random-song-count">Number of songs</label>
          <input
            id="random-song-count"
            type="number"
            min="1"
            max="50"
            class="input mt-1"
            bind:value={local.random_song_count}
            disabled={readonly}
          />
        </div>
      {:else}
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
      {/if}
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

    {#if local.game_mode !== 'random'}
      <ArtistMultiSelect bind:selected={local.required_artists} {readonly} />
    {/if}
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
        Gradual reveal
      </label>
    </div>

    <div>
      <label class="label" for="obscure-mode">Reveal effect</label>
      <select
        id="obscure-mode"
        class="input mt-1"
        bind:value={local.album_art_obscure_mode}
        disabled={readonly ||
          !local.album_art_enabled ||
          !local.album_art_unblur}
      >
        <option value="blur">Gaussian blur</option>
        <option value="pixelate">Pixelation</option>
      </select>
    </div>

    <div>
      <label class="label" for="blurs">
        {local.album_art_obscure_mode === 'pixelate'
          ? 'Pixel grid size per bracket'
          : 'Blur radius per bracket (pixels)'}
      </label>
      <input
        id="blurs"
        class="input mt-1 font-mono"
        bind:value={blursText}
        placeholder={local.album_art_obscure_mode === 'pixelate'
          ? 'leave empty for the default 4 → 256 ramp'
          : 'leave empty for the default 24 → 0 ramp'}
        disabled={readonly ||
          !local.album_art_enabled ||
          !local.album_art_unblur}
      />
      {#if local.album_art_obscure_mode === 'pixelate'}
        <p class="mt-1 text-xs text-text-muted">
          Optional. One value (1–256) per bracket — e.g. <code
            class="text-text-secondary">4, 8, 16, 32, 64, 256</code
          > pairs with a 6-bracket round. Empty means linear from 4 up to 256.
          Each value is the side length of the down-sampled cover before it's
          stretched back to <strong>256×256</strong>: <code
            class="text-text-secondary">1</code
          > is a single solid colour, <code class="text-text-secondary">4</code> is
          a 4×4 mosaic, <code class="text-text-secondary">16</code> is recognizably
          chunky, and <code class="text-text-secondary">256</code> is fully sharp.
        </p>
      {:else}
        <p class="mt-1 text-xs text-text-muted">
          Optional. One value (0–256) per bracket — e.g. <code
            class="text-text-secondary">24, 18, 12, 6, 2, 0</code
          > pairs with a 6-bracket round. Empty means linear from 24 down to 0.
          Values are CSS blur radii in pixels applied to the guesser's
          <strong>256×256</strong> cover: ~24px is heavily obscured, ~8px reads
          as a soft haze, and 0 is fully sharp.
        </p>
      {/if}
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
