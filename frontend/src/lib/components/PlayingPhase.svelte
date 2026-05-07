<script lang="ts">
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { room } from '$lib/ws.svelte';
  import type { Room, SongCandidate } from '$lib/types';
  import RoundIntermission from './RoundIntermission.svelte';

  let { roomData }: { roomData: Room } = $props();

  let query = $state('');
  let searchResults = $state<SongCandidate[]>([]);
  let searching = $state(false);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  let lastResult = $state<{ correct: boolean; points: number } | null>(null);
  let submitting = $state(false);
  let error = $state<string | null>(null);

  let audio: HTMLAudioElement | null = $state(null);
  let playing = $state(false);
  let lastPlayedAt = $state<number | null>(null);

  const meId = $derived(auth.user?.id ?? '');
  const active = $derived(room.activeRound);
  const isPicker = $derived(!!active && active.picker_ids.includes(meId));
  const myBracket = $derived(room.bracketIndices[meId] ?? 0);
  const myFinished = $derived(room.finishedPlayers[meId] ?? null);
  const brackets = $derived(active?.guess_brackets_seconds ?? []);
  const pickerNames = $derived(
    active
      ? active.picker_ids.map(
          (id) =>
            roomData.players.find((p) => p.user.id === id)?.user.username ??
            'someone'
        )
      : []
  );
  const pickerLabel = $derived(formatList(pickerNames));

  function formatList(items: string[]): string {
    if (items.length === 0) return 'someone';
    if (items.length === 1) return items[0];
    if (items.length === 2) return `${items[0]} and ${items[1]}`;
    return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
  }
  const blurAmount = $derived(
    roomData.settings.album_art_unblur && brackets.length > 0
      ? Math.max(0, 24 - 24 * (myBracket / Math.max(1, brackets.length - 1)))
      : 0
  );

  $effect(() => {
    // When the active round changes, reset local state.
    active;
    query = '';
    searchResults = [];
    lastResult = null;
    lastPlayedAt = null;
    playing = false;
  });

  async function play() {
    if (!audio || !active) return;
    // Cache-bust so the browser refetches on every play (server slices fresh
    // bytes based on our current bracket).
    audio.src = `${active.audio_url}?t=${Date.now()}`;
    try {
      await audio.play();
      playing = true;
      lastPlayedAt = Date.now();
    } catch (e) {
      error = String(e);
    }
  }

  function onAudioEnded() {
    playing = false;
  }

  function onQueryInput() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  }

  async function runSearch() {
    const q = query.trim();
    if (!q) {
      searchResults = [];
      return;
    }
    searching = true;
    error = null;
    try {
      // Pass the room code so the search applies the room's rules (popularity,
      // required artists). No point letting players guess songs the picker
      // couldn't have submitted.
      searchResults = await api.spotifySearch(q, roomData.code);
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      searching = false;
    }
  }

  async function pickGuess(track: SongCandidate) {
    if (!active) return;
    submitting = true;
    error = null;
    try {
      const r = await api.guess(
        roomData.code,
        active.round_id,
        track.spotify_track_id
      );
      lastResult = { correct: r.correct, points: r.points };
      query = '';
      searchResults = [];
    } catch (err) {
      error = err instanceof APIError ? err.message : String(err);
    } finally {
      submitting = false;
    }
  }

  async function skip() {
    if (!active) return;
    submitting = true;
    error = null;
    try {
      await api.skip(roomData.code, active.round_id);
      lastResult = null;
    } catch (err) {
      error = err instanceof APIError ? err.message : String(err);
    } finally {
      submitting = false;
    }
  }

  // Round-ended interstitial
  const interstitial = $derived(room.lastRoundResult);
</script>

{#if interstitial && !active}
  <div class="mx-auto max-w-2xl">
    <RoundIntermission result={interstitial} {meId} />
  </div>
{:else if active}
  <div class="mx-auto max-w-2xl">
    <div class="space-y-4">
      {#if isPicker}
        <div class="card text-center">
          <p class="text-sm text-text-muted">Your song is up</p>
          <h2 class="mt-2 text-xl font-semibold">Watching the others guess</h2>
          {#if active.album_image_url}
            <img
              src={active.album_image_url}
              alt=""
              class="mx-auto mt-6 h-48 w-48 rounded shadow-xl"
            />
          {/if}
        </div>

        <div class="card">
          <h3 class="mb-3 text-sm font-semibold text-text-secondary">
            Attempts so far
          </h3>
          {#if room.pickerAttempts.length === 0}
            <p class="text-sm text-text-muted">No guesses yet.</p>
          {:else}
            <ul class="divide-y divide-border">
              {#each room.pickerAttempts as a, i (i)}
                {@const username =
                  roomData.players.find((p) => p.user.id === a.user_id)?.user
                    .username ?? a.user_id.slice(0, 6)}
                <li class="flex items-center justify-between py-2 text-sm">
                  <span>
                    <span class="font-medium">{username}</span>
                    <span class="text-text-muted">
                      @ {brackets[a.bracket_index]}s
                    </span>
                  </span>
                  <span>
                    {#if a.kind === 'skip'}
                      <span class="text-text-muted">skipped</span>
                    {:else if a.correct}
                      <span class="text-success">{a.guess_text} ✓</span>
                    {:else}
                      <span class="text-warning">{a.guess_text} ✗</span>
                    {/if}
                  </span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {:else}
        <div class="card flex flex-col items-center gap-4">
          <p class="text-sm text-text-muted">
            Picked by <span class="text-text-primary">{pickerLabel}</span>
          </p>

          {#if active.album_image_url}
            <img
              src={active.album_image_url}
              alt=""
              class="h-64 w-64 rounded shadow-xl transition-[filter] duration-700"
              style="filter: blur({blurAmount}px)"
            />
          {/if}

          <div class="flex w-full items-center justify-center gap-2">
            {#each brackets as b, i}
              <span
                class="badge font-mono"
                class:bg-accent={i === myBracket && !myFinished}
                class:text-text-primary={i === myBracket && !myFinished}
                class:bg-surface-raised={i !== myBracket || myFinished}
                class:text-text-muted={i > myBracket || myFinished}
                class:line-through={i < myBracket}
              >
                {b}s
              </span>
            {/each}
          </div>

          <audio bind:this={audio} onended={onAudioEnded}></audio>

          {#if myFinished}
            {#if myFinished.outcome === 'correct'}
              <p class="text-success">
                Correct! +{myFinished.points}
                {myFinished.points === 1 ? 'pt' : 'pts'}
              </p>
            {:else}
              <p class="text-warning">Out of brackets. 0 pts.</p>
            {/if}
          {:else}
            <button class="btn-primary px-8" onclick={play} disabled={playing}>
              {playing ? 'Playing…' : `Play ${brackets[myBracket]}s`}
            </button>
          {/if}
        </div>

        {#if !myFinished}
          <div class="card space-y-3">
            <input
              class="input"
              placeholder="Search the song you think it is…"
              bind:value={query}
              oninput={onQueryInput}
              disabled={submitting}
            />
            {#if searching}
              <p class="text-xs text-text-muted">searching…</p>
            {/if}
            {#if searchResults.length > 0}
              <ul
                class="max-h-72 overflow-y-auto rounded-md border border-border bg-surface-raised"
              >
                {#each searchResults as r (r.spotify_track_id)}
                  <li>
                    <button
                      type="button"
                      class="flex w-full items-center gap-3 p-2 text-left hover:bg-surface disabled:opacity-50"
                      disabled={submitting}
                      onclick={() => pickGuess(r)}
                    >
                      {#if r.album_image_url}
                        <img
                          src={r.album_image_url}
                          alt=""
                          class="h-10 w-10 flex-shrink-0 rounded"
                        />
                      {:else}
                        <div
                          class="h-10 w-10 flex-shrink-0 rounded bg-surface"
                        ></div>
                      {/if}
                      <span class="min-w-0 flex-1">
                        <span class="block truncate font-medium">{r.title}</span>
                        <span
                          class="block truncate text-sm text-text-secondary"
                        >
                          {r.artist}
                        </span>
                      </span>
                    </button>
                  </li>
                {/each}
              </ul>
            {/if}
            {#if lastResult && !lastResult.correct}
              <p class="text-sm text-warning">Not quite — bracket advanced.</p>
            {/if}
            {#if error}
              <p class="text-sm text-danger">{error}</p>
            {/if}
            <button
              type="button"
              class="btn-ghost w-full"
              disabled={submitting}
              onclick={skip}
            >
              Skip →
            </button>
          </div>
        {/if}
      {/if}
    </div>
  </div>
{:else}
  <div class="card text-center text-text-secondary">
    <p>Setting up the next round…</p>
  </div>
{/if}
