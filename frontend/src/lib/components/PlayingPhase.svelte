<script lang="ts">
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { room } from '$lib/ws.svelte';
  import type { Room, SongCandidate } from '$lib/types';
  import RoundIntermission from './RoundIntermission.svelte';

  let { roomData }: { roomData: Room } = $props();

  type LocalAttempt = {
    kind: 'guess' | 'skip';
    bracket_index: number;
    guess_text: string | null;
    correct: boolean | null;
    hint_fulfilled: boolean;
  };

  let query = $state('');
  let searchResults = $state<SongCandidate[]>([]);
  let searching = $state(false);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  let lastResult = $state<{ correct: boolean; points: number } | null>(null);
  let myAttempts = $state<LocalAttempt[]>([]);
  let submitting = $state(false);
  let error = $state<string | null>(null);

  let audio: HTMLAudioElement | null = $state(null);
  let playing = $state(false);
  let playbackSec = $state(0);
  let srcBracket: number | null = null;
  let rafId: number | null = null;
  let now = $state(Date.now());

  const meId = $derived(auth.user?.id ?? '');
  const active = $derived(room.activeRound);
  const isPicker = $derived(!!active && active.picker_ids.includes(meId));
  const myBracket = $derived(room.bracketIndices[meId] ?? 0);
  const myFinished = $derived(room.finishedPlayers[meId] ?? null);
  const brackets = $derived(active?.guess_brackets_seconds ?? []);
  const maxBracketSec = $derived(brackets[brackets.length - 1] || 1);
  const currentBracketSec = $derived(
    brackets[Math.min(myBracket, brackets.length - 1)] ?? 0
  );
  const fillPct = $derived(
    Math.min(100, (playbackSec / maxBracketSec) * 100)
  );
  const roundDeadline = $derived(
    active
      ? new Date(active.started_at_server).getTime() +
          active.round_max_seconds * 1000
      : null
  );
  const secondsLeft = $derived(
    roundDeadline !== null
      ? Math.max(0, Math.ceil((roundDeadline - now) / 1000))
      : null
  );
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

  function bracketPct(i: number): number {
    if (brackets.length === 0) return 0;
    const idx = Math.max(0, Math.min(i, brackets.length - 1));
    return Math.min(100, Math.max(0, (brackets[idx] / maxBracketSec) * 100));
  }

  function pickerAttemptColor(a: {
    kind: string;
    correct: boolean | null;
    hint_fulfilled: boolean;
  }): string {
    if (a.kind === 'skip') return 'text-text-muted';
    if (a.correct) return 'text-success';
    if (a.hint_fulfilled) return 'text-warning';
    return 'text-danger';
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
    myAttempts = [];
    playbackSec = 0;
    srcBracket = null;
    if (audio && !audio.paused) audio.pause();
    playing = false;
  });

  $effect(() => {
    // When the player's bracket changes, the previously-fetched audio slice
    // is stale — discard it so the next play() refetches a longer slice.
    myBracket;
    if (audio && !audio.paused) audio.pause();
    playbackSec = 0;
    srcBracket = null;
  });

  $effect(() => {
    if (!active) return;
    const id = setInterval(() => {
      now = Date.now();
    }, 250);
    return () => clearInterval(id);
  });

  function tickPlayback() {
    if (audio) playbackSec = audio.currentTime;
    rafId = requestAnimationFrame(tickPlayback);
  }

  function startTracking() {
    if (rafId == null) rafId = requestAnimationFrame(tickPlayback);
  }

  function stopTracking() {
    if (rafId != null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  async function togglePlayback() {
    if (!audio || !active) return;
    if (playing) {
      audio.pause();
      return;
    }
    // Cache-bust so the server slices fresh bytes for the current bracket.
    // Only refetch when the bracket changed since the last load, so a pause
    // can be resumed without restarting. After a natural end, just rewind.
    if (srcBracket !== myBracket) {
      audio.src = `${active.audio_url}?t=${Date.now()}`;
      srcBracket = myBracket;
      playbackSec = 0;
    } else if (audio.ended) {
      audio.currentTime = 0;
      playbackSec = 0;
    }
    try {
      await audio.play();
    } catch (e) {
      error = String(e);
    }
  }

  function onAudioPlay() {
    playing = true;
    startTracking();
  }

  function onAudioPause() {
    playing = false;
    stopTracking();
    if (audio) playbackSec = audio.currentTime;
  }

  function onAudioEnded() {
    playing = false;
    stopTracking();
    if (audio) playbackSec = audio.currentTime;
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
    const priorBracket = myBracket;
    try {
      const r = await api.guess(
        roomData.code,
        active.round_id,
        track.spotify_track_id
      );
      lastResult = { correct: r.correct, points: r.points };
      myAttempts = [
        ...myAttempts,
        {
          kind: 'guess',
          bracket_index: priorBracket,
          guess_text: `${track.title} — ${track.artist}`,
          correct: r.correct,
          hint_fulfilled: r.hint_fulfilled,
        },
      ];
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
    const priorBracket = myBracket;
    try {
      await api.skip(roomData.code, active.round_id);
      lastResult = null;
      myAttempts = [
        ...myAttempts,
        {
          kind: 'skip',
          bracket_index: priorBracket,
          guess_text: null,
          correct: null,
          hint_fulfilled: false,
        },
      ];
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
          {#if secondsLeft !== null}
            <p
              class="mt-2 font-mono text-sm"
              class:text-text-muted={secondsLeft > 10}
              class:text-warning={secondsLeft <= 10 && secondsLeft > 3}
              class:text-danger={secondsLeft <= 3}
            >
              {secondsLeft}s left
            </p>
          {/if}
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
                  <span class={pickerAttemptColor(a)}>
                    {#if a.kind === 'skip'}
                      skipped
                    {:else if a.correct}
                      {a.guess_text} ✓
                    {:else if a.hint_fulfilled}
                      {a.guess_text} (hint)
                    {:else}
                      {a.guess_text} ✗
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

          {#if secondsLeft !== null}
            <p
              class="font-mono text-sm"
              class:text-text-muted={secondsLeft > 10}
              class:text-warning={secondsLeft <= 10 && secondsLeft > 3}
              class:text-danger={secondsLeft <= 3}
            >
              {secondsLeft}s left
            </p>
          {/if}

          {#if active.album_image_url}
            <img
              src={active.album_image_url}
              alt=""
              class="h-64 w-64 rounded shadow-xl transition-[filter] duration-700"
              style="filter: blur({blurAmount}px)"
            />
          {/if}

          <!-- Past attempts: stacked vertically and centered between cover and bar -->
          {#if myAttempts.length > 0}
            <ul class="flex w-full flex-col items-center gap-1.5">
              {#each myAttempts as a, i (i)}
                <li
                  class="w-full max-w-md truncate rounded-md px-4 py-2 text-center text-sm font-medium"
                  class:bg-success={a.kind === 'guess' && a.correct}
                  class:bg-warning={a.kind === 'guess' &&
                    !a.correct &&
                    a.hint_fulfilled}
                  class:bg-danger={a.kind === 'guess' &&
                    !a.correct &&
                    !a.hint_fulfilled}
                  class:bg-surface-raised={a.kind === 'skip'}
                  class:text-text-muted={a.kind === 'skip'}
                  class:text-bg={a.kind === 'guess'}
                  title={a.kind === 'skip' ? 'Skipped' : (a.guess_text ?? '')}
                >
                  {a.kind === 'skip' ? 'Skipped' : a.guess_text}
                </li>
              {/each}
            </ul>
          {/if}

          {#if brackets.length > 0}
            <div class="w-full px-2">
              <!-- Single time label, anchored at the current bracket -->
              <div class="relative mb-1 h-4">
                <span
                  class="absolute top-0 -translate-x-1/2 transform font-mono text-xs font-semibold text-text-primary"
                  style="left: {bracketPct(myBracket)}%"
                >
                  {currentBracketSec}s
                </span>
              </div>

              <!-- Progress bar with vertical separator lines at each bracket -->
              <div
                class="relative h-3 overflow-hidden rounded bg-surface-raised"
              >
                <div
                  class="h-full bg-accent"
                  style="width: {fillPct}%"
                ></div>
                {#each brackets as _b, i (i)}
                  {@const pos = bracketPct(i)}
                  <div
                    class="absolute top-0 h-full w-px bg-border"
                    style="left: {pos}%"
                  ></div>
                {/each}
              </div>
            </div>
          {/if}

          <audio
            bind:this={audio}
            onplay={onAudioPlay}
            onpause={onAudioPause}
            onended={onAudioEnded}
          ></audio>

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
            <button
              type="button"
              class="btn-primary h-14 w-14 rounded-full p-0"
              onclick={togglePlayback}
              aria-label={playing ? 'Pause' : 'Play'}
            >
              {#if playing}
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  class="h-6 w-6"
                  aria-hidden="true"
                >
                  <line x1="10" x2="10" y1="4" y2="20" />
                  <line x1="14" x2="14" y1="4" y2="20" />
                </svg>
              {:else}
                <svg
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  class="h-6 w-6"
                  aria-hidden="true"
                >
                  <polygon points="6 3 20 12 6 21 6 3" />
                </svg>
              {/if}
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
