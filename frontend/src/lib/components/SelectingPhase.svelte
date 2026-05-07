<script lang="ts">
  import { api, APIError } from '$lib/api';
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

  const isLeader = $derived(auth.user?.id === roomData.leader_id);
  const quota = $derived(roomData.settings.songs_per_player);
  const allReady = $derived(
    roomData.players.every((p) => p.songs_submitted >= quota)
  );

  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  function onQueryInput() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  }

  async function runSearch() {
    const q = query.trim();
    if (q.length < 2) {
      results = [];
      return;
    }
    searching = true;
    error = null;
    try {
      results = await api.spotifySearch(q, roomData.code);
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      searching = false;
    }
  }

  async function loadMine() {
    try {
      mySongs = await api.mySongs(roomData.code);
    } catch (e) {
      console.error('failed to load my songs', e);
    }
  }

  $effect(() => {
    loadMine();
  });

  async function submit(track: SongCandidate) {
    submitting = track.spotify_track_id;
    error = null;
    try {
      await api.submitSong(roomData.code, track.spotify_track_id);
      await loadMine();
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      submitting = null;
    }
  }

  async function remove(songId: string) {
    try {
      await api.deleteSong(roomData.code, songId);
      await loadMine();
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    }
  }

  async function startGame() {
    starting = true;
    error = null;
    try {
      await api.changePhase(roomData.code, 'playing');
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      starting = false;
    }
  }
</script>

<div class="grid gap-6 md:grid-cols-[2fr_1fr]">
  <div class="space-y-4">
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
              {#if r.album_image_url}
                <img
                  src={r.album_image_url}
                  alt=""
                  class="h-12 w-12 flex-shrink-0 rounded"
                />
              {:else}
                <div class="h-12 w-12 flex-shrink-0 rounded bg-surface"></div>
              {/if}
              <div class="min-w-0 flex-1">
                <p class="truncate font-medium">{r.title}</p>
                <p class="truncate text-sm text-text-secondary">
                  {r.artist}{#if r.album} · {r.album}{/if}
                </p>
              </div>
              <span class="text-xs text-text-muted">{r.popularity ?? '?'}</span>
              <button
                class="btn-secondary text-sm"
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
              {#if s.album_image_url}
                <img src={s.album_image_url} alt="" class="h-10 w-10 rounded" />
              {/if}
              <div class="flex-1">
                <p class="font-medium">{s.title}</p>
                <p class="text-sm text-text-secondary">{s.artist}</p>
              </div>
              <button
                class="btn-ghost text-sm text-danger"
                onclick={() => remove(s.id)}>Remove</button
              >
            </li>
          {/each}
        </ul>
      {/if}
    </div>
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
        disabled={starting || !allReady}
        onclick={startGame}
        title={allReady ? '' : 'waiting for everyone to finish picking'}
      >
        {starting ? 'Starting…' : allReady ? 'Start the game' : 'Waiting for picks'}
      </button>
    {/if}
  </div>
</div>
