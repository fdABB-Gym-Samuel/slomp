<script lang="ts">
  import { api, APIError } from '$lib/api';
  import { searchSongs } from '$lib/deezer';
  import { auth } from '$lib/auth.svelte';
  import type { Room, SongCandidate, SubmittedSong } from '$lib/types';
  import PlayerList from './PlayerList.svelte';

  let { roomData }: { roomData: Room } = $props();

  let query = $state('');
  let results = $state<SongCandidate[]>([]);
  let searching = $state(false);
  let mySongs = $state<SubmittedSong[]>([]);
  let submitting = $state<string | null>(null);
  let error = $state<string | null>(null);
  let starting = $state(false);

  // Inline preview player for the picking phase. Plays Deezer's preview MP3
  // straight from cdnt-preview.dzcdn.net so no audio bytes go through our
  // backend. One shared <audio> element; `previewPlayingId` is the
  // spotify_track_id (Deezer id) of whatever's currently audible.
  let audio: HTMLAudioElement | null = $state(null);
  let previewPlayingId = $state<string | null>(null);

  function togglePreview(trackId: string, previewUrl: string | null) {
    if (!previewUrl || !audio) return;
    if (previewPlayingId === trackId) {
      audio.pause();
      return;
    }
    audio.src = previewUrl;
    previewPlayingId = trackId;
    audio.play().catch(() => {
      previewPlayingId = null;
    });
  }

  function onPreviewPause() {
    previewPlayingId = null;
  }

  const isLeader = $derived(auth.user?.id === roomData.leader_id);
  const quota = $derived(roomData.settings.songs_per_player);
  const isSpectator = $derived(
    roomData.players.find((p) => p.user.id === auth.user?.id)?.spectating ?? false
  );
  const allReady = $derived(
    roomData.players.every((p) => p.spectating || p.songs_submitted >= quota)
  );
  const enoughActive = $derived(
    roomData.players.filter((p) => !p.spectating).length >= 2
  );

  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  function onQueryInput() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  }

  async function runSearch() {
    const q = query.trim();
    if (q.length < 1) {
      results = [];
      return;
    }
    searching = true;
    error = null;
    try {
      results = await searchSongs(q, roomData.settings);
    } catch (e) {
      error = e instanceof APIError ? e.message : (e as Error).message;
    } finally {
      searching = false;
    }
  }

  async function loadMine() {
    try {
      mySongs = await api.mySongs(roomData.id);
    } catch (e) {
      console.error('failed to load my songs', e);
    }
  }

  $effect(() => {
    if (!isSpectator) loadMine();
  });

  async function submit(track: SongCandidate) {
    submitting = track.spotify_track_id;
    error = null;
    if (audio && previewPlayingId === track.spotify_track_id) audio.pause();
    try {
      await api.submitSong(roomData.id, track.spotify_track_id);
      await loadMine();
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      submitting = null;
    }
  }

  async function remove(songId: string) {
    const target = mySongs.find((m) => m.id === songId);
    if (audio && target && previewPlayingId === target.spotify_track_id) {
      audio.pause();
    }
    try {
      await api.deleteSong(roomData.id, songId);
      await loadMine();
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    }
  }

  async function startGame() {
    starting = true;
    error = null;
    try {
      await api.changePhase(roomData.id, 'playing');
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      starting = false;
    }
  }
</script>

<div class="grid gap-6 md:grid-cols-[2fr_1fr]">
  <div class="space-y-4">
    {#if isSpectator}
      <div class="card text-center">
        <h2 class="text-lg font-semibold">You joined mid-game</h2>
        <p class="mt-2 text-sm text-text-secondary">
          The current game is in song selection. You'll join in for the next
          game once this one wraps up.
        </p>
      </div>
    {:else}
    <div class="card">
      <h2 class="mb-3 text-lg font-semibold">
        Find songs ({mySongs.length} / {quota})
      </h2>
      <input
        class="input"
        placeholder="Search artist or title…"
        bind:value={query}
        oninput={onQueryInput}
        disabled={mySongs.length >= quota}
      />
      {#if searching}
        <p class="mt-3 text-sm text-text-muted">searching…</p>
      {/if}
      {#if error}
        <p class="mt-3 text-sm text-danger">{error}</p>
      {/if}
      {#if results.length > 0 && mySongs.length < quota}
        <ul class="mt-4 space-y-2">
          {#each results as r (r.spotify_track_id)}
            <li
              class="flex items-center gap-3 rounded-md border border-border bg-surface-raised p-3"
            >
              <div class="relative h-12 w-12 flex-shrink-0 overflow-hidden rounded">
                {#if r.album_image_url}
                  <img src={r.album_image_url} alt="" class="h-full w-full" />
                {:else}
                  <div class="h-full w-full bg-surface"></div>
                {/if}
                {#if r.preview_url}
                  {@const isPlaying = previewPlayingId === r.spotify_track_id}
                  <button
                    type="button"
                    class="absolute inset-0 flex items-center justify-center bg-black/50 text-white transition-opacity hover:opacity-100 focus:opacity-100"
                    class:opacity-0={!isPlaying}
                    class:opacity-100={isPlaying}
                    onclick={() => togglePreview(r.spotify_track_id, r.preview_url)}
                    aria-label={isPlaying ? 'Pause preview' : 'Play preview'}
                  >
                    {#if isPlaying}
                      <svg viewBox="0 0 24 24" fill="currentColor" class="h-5 w-5" aria-hidden="true">
                        <rect x="6" y="5" width="4" height="14" rx="1" />
                        <rect x="14" y="5" width="4" height="14" rx="1" />
                      </svg>
                    {:else}
                      <svg viewBox="0 0 24 24" fill="currentColor" class="h-5 w-5" aria-hidden="true">
                        <polygon points="6 4 20 12 6 20 6 4" />
                      </svg>
                    {/if}
                  </button>
                {/if}
              </div>
              <div class="min-w-0 flex-1">
                <p class="truncate font-medium">{r.title}</p>
                <p class="truncate text-sm text-text-secondary">
                  {r.artist}{#if r.album} · {r.album}{/if}
                </p>
              </div>
              <span class="flex-shrink-0 text-xs text-text-muted">{r.popularity ?? '?'}</span>
              <button
                class="btn-secondary flex-shrink-0 text-sm"
                disabled={submitting !== null ||
                  mySongs.some((m) => m.spotify_track_id === r.spotify_track_id)}
                onclick={() => submit(r)}
              >
                {submitting === r.spotify_track_id ? 'Adding…' : 'Pick'}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="card">
      <h2 class="mb-3 text-lg font-semibold">Your picks</h2>
      {#if mySongs.length === 0}
        <p class="text-sm text-text-muted">No songs yet — search above.</p>
      {:else}
        <ul class="space-y-2">
          {#each mySongs as s (s.id)}
            <li
              class="flex items-center gap-3 rounded-md border border-border bg-surface-raised p-3"
            >
              <div class="relative h-10 w-10 flex-shrink-0 overflow-hidden rounded">
                {#if s.album_image_url}
                  <img src={s.album_image_url} alt="" class="h-full w-full" />
                {:else}
                  <div class="h-full w-full bg-surface"></div>
                {/if}
                {#if s.preview_url}
                  {@const isPlaying = previewPlayingId === s.spotify_track_id}
                  <button
                    type="button"
                    class="absolute inset-0 flex items-center justify-center bg-black/50 text-white transition-opacity hover:opacity-100 focus:opacity-100"
                    class:opacity-0={!isPlaying}
                    class:opacity-100={isPlaying}
                    onclick={() => togglePreview(s.spotify_track_id, s.preview_url)}
                    aria-label={isPlaying ? 'Pause preview' : 'Play preview'}
                  >
                    {#if isPlaying}
                      <svg viewBox="0 0 24 24" fill="currentColor" class="h-4 w-4" aria-hidden="true">
                        <rect x="6" y="5" width="4" height="14" rx="1" />
                        <rect x="14" y="5" width="4" height="14" rx="1" />
                      </svg>
                    {:else}
                      <svg viewBox="0 0 24 24" fill="currentColor" class="h-4 w-4" aria-hidden="true">
                        <polygon points="6 4 20 12 6 20 6 4" />
                      </svg>
                    {/if}
                  </button>
                {/if}
              </div>
              <div class="min-w-0 flex-1">
                <p class="truncate font-medium">{s.title}</p>
                <p class="truncate text-sm text-text-secondary">{s.artist}</p>
              </div>
              <button
                class="btn-ghost flex-shrink-0 text-sm text-danger"
                onclick={() => remove(s.id)}>Remove</button
              >
            </li>
          {/each}
        </ul>
      {/if}
    </div>
    <audio bind:this={audio} onpause={onPreviewPause} onended={onPreviewPause}></audio>
    {/if}
  </div>

  <div class="space-y-4">
    <div class="card">
      <h2 class="mb-3 text-lg font-semibold">Players</h2>
      <PlayerList
        players={roomData.players}
        leaderId={roomData.leader_id}
        songsPerPlayer={quota}
        showSubmissions
        highlightUserId={auth.user?.id ?? null}
      />
    </div>

    {#if isLeader}
      <button
        class="btn-primary w-full"
        disabled={starting || !allReady || !enoughActive}
        onclick={startGame}
        title={!enoughActive
          ? 'need at least 2 active players (spectators don\'t count)'
          : !allReady
            ? 'waiting for everyone to finish picking'
            : ''}
      >
        {starting
          ? 'Starting…'
          : !enoughActive
            ? 'Need at least 2 players'
            : !allReady
              ? 'Waiting for picks'
              : 'Start the game'}
      </button>
    {/if}
  </div>
</div>
