<script lang="ts">
  import { api, APIError } from '$lib/api';
  import type { Room, RoomSettings } from '$lib/types';
  import ArtistMultiSelect from './ArtistMultiSelect.svelte';

  let { roomData }: { roomData: Room } = $props();

  let local = $state<RoomSettings | null>(null);
  let bracketsText = $state('');
  let saving = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    local = { ...roomData.settings };
    bracketsText = roomData.settings.guess_brackets_seconds.join(', ');
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
      await api.updateSettings(roomData.code, {
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
<div class="card space-y-4">
  <h2 class="text-lg font-semibold">Settings</h2>

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
      />
    </div>
  </div>

  <div>
    <label class="label" for="brackets">Guess brackets (seconds)</label>
    <input
      id="brackets"
      class="input mt-1 font-mono"
      bind:value={bracketsText}
      placeholder="0.5, 1, 2.5, 5, 15, 30"
    />
    <p class="mt-1 text-xs text-text-muted">
      Strictly ascending. Last value caps the round time.
    </p>
  </div>

  <ArtistMultiSelect bind:selected={local.required_artists} />

  <div class="flex gap-4">
    <label class="flex items-center gap-2 text-sm">
      <input type="checkbox" bind:checked={local.album_art_enabled} />
      Show album art
    </label>
    <label class="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        bind:checked={local.album_art_unblur}
        disabled={!local.album_art_enabled}
      />
      Gradual unblur
    </label>
  </div>

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
      />
      <p class="mt-1 text-xs text-text-muted">
        When this elapses, anyone still guessing is auto-exhausted with 0 pts.
      </p>
    </div>
  </div>

  {#if error}
    <p class="text-sm text-danger">{error}</p>
  {/if}

  <button class="btn-primary w-full" disabled={saving} onclick={save}>
    {saving ? 'Saving…' : 'Save settings'}
  </button>
</div>
{/if}
