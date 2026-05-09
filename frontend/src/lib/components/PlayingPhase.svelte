<script lang="ts">
  import { api, APIError } from '$lib/api';
  import { searchSongs } from '$lib/deezer';
  import { auth } from '$lib/auth.svelte';
  import { room } from '$lib/ws.svelte';
  import type { Room, SongCandidate } from '$lib/types';
  import RoundIntermission from './RoundIntermission.svelte';

  let { roomData }: { roomData: Room } = $props();

  let query = $state('');
  let searchResults = $state<SongCandidate[]>([]);
  let searching = $state(false);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let highlightedIndex = $state(0);
  let resultListEl: HTMLUListElement | null = $state(null);

  let lastResult = $state<{ correct: boolean; points: number } | null>(null);
  // The player's own guess/skip history for the active round lives on the
  // shared room store so a mid-round reconnect can re-hydrate it from the
  // server-replayed `my_attempts` event.
  const myAttempts = $derived(room.myAttempts);
  let submitting = $state(false);
  let error = $state<string | null>(null);
  let showMatchingHelp = $state(false);

  let audio: HTMLAudioElement | null = $state(null);
  let playing = $state(false);
  let playbackSec = $state(0);
  let srcBracket: number | null = null;
  let rafId: number | null = null;
  let now = $state(Date.now());

  const meId = $derived(auth.user?.id ?? '');
  const active = $derived(room.activeRound);
  const isPicker = $derived(!!active && active.picker_ids.includes(meId));
  const isSpectator = $derived(
    roomData.players.find((p) => p.user.id === meId)?.spectating ?? false
  );
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
  // Continuous 0..1 ratio of round time remaining, used for smooth color
  // interpolation independently of the integer seconds shown in the UI.
  const timeRatio = $derived.by(() => {
    if (!active || roundDeadline === null) return 1;
    const totalMs = active.round_max_seconds * 1000;
    return Math.max(0, Math.min(1, (roundDeadline - now) / totalMs));
  });
  const timerColor = $derived.by(() => {
    const r = timeRatio;
    // Hue glides from a calm yellow (~50°) at full time down to red (0°).
    // Saturation and darkness ramp up near the end so the badge feels urgent.
    const hue = r * 50;
    const sat = 25 + (1 - r) * 60;
    const light = 65 - (1 - r) * 10;
    return `hsl(${hue}, ${sat}%, ${light}%)`;
  });
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


  $effect(() => {
    // When the active round changes, reset local state. (myAttempts lives
    // on the room store and is reset there by the round_started handler.)
    active;
    query = '';
    searchResults = [];
    lastResult = null;
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

  $effect(() => {
    searchResults;
    highlightedIndex = 0;
  });

  $effect(() => {
    if (!resultListEl) return;
    const el = resultListEl.children[highlightedIndex] as HTMLElement | undefined;
    el?.scrollIntoView({ block: 'nearest' });
  });

  function onSearchKeyDown(e: KeyboardEvent) {
    if (searchResults.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      highlightedIndex = (highlightedIndex + 1) % searchResults.length;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      highlightedIndex =
        (highlightedIndex - 1 + searchResults.length) % searchResults.length;
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (submitting) return;
      const r = searchResults[highlightedIndex];
      if (r) pickGuess(r);
    }
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
      // Apply the room's rules (popularity, required artists) so we don't
      // suggest tracks the picker couldn't have submitted.
      searchResults = await searchSongs(q, roomData.settings);
    } catch (e) {
      error = e instanceof APIError ? e.message : (e as Error).message;
    } finally {
      searching = false;
    }
  }

  async function pickGuess(track: SongCandidate) {
    if (!active) return;
    submitting = true;
    error = null;
    const priorBracket = myBracket;
    // Capture the round we're guessing against — if the round transitions
    // (round_started clears myAttempts) before the response lands, dropping
    // this update prevents the stale guess from leaking into the next round.
    const guessRoundId = active.round_id;
    try {
      const r = await api.guess(
        roomData.id,
        active.round_id,
        track.spotify_track_id
      );
      if (room.activeRound?.round_id !== guessRoundId) return;
      lastResult = { correct: r.correct, points: r.points };
      room.myAttempts = [
        ...room.myAttempts,
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
    const guessRoundId = active.round_id;
    try {
      await api.skip(roomData.id, active.round_id);
      if (room.activeRound?.round_id !== guessRoundId) return;
      lastResult = null;
      room.myAttempts = [
        ...room.myAttempts,
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
      {#if isSpectator}
        <div class="card text-center">
          <p class="text-sm text-text-muted">Spectating</p>
          <h2 class="mt-2 text-xl font-semibold">Game in progress</h2>
          <p class="mt-2 text-sm text-text-secondary">
            You'll join in for the next game once this one wraps up.
          </p>
          {#if secondsLeft !== null}
            <p class="mt-2 font-mono text-xs text-text-muted">
              round ends in {secondsLeft}s
            </p>
          {/if}
        </div>
      {:else if isPicker}
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
        <div class="card relative flex flex-col items-center gap-4">
          {#if secondsLeft !== null}
            <div
              class="absolute left-4 top-4 flex items-center gap-1.5 font-mono text-sm font-semibold transition-colors duration-300"
              style="color: {timerColor}"
              aria-label="Time left in round"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                class="h-4 w-4"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="9" />
                <polyline points="12 7 12 12 15 14" />
              </svg>
              <span>{secondsLeft}s</span>
            </div>
          {/if}

          {#if active.picker_ids.length > 0}
            <p class="text-sm text-text-muted">
              Picked by <span class="text-text-primary">{pickerLabel}</span>
            </p>
          {/if}

          {#if active.album_image_url}
            <!-- The proxy applies the blur server-side keyed off the
                 requesting player's bracket, so the unblurred bytes never
                 reach the wire. The ?b=… suffix is purely a cache-bust so
                 the browser refetches when we cross into a new bracket. -->
            <div class="h-64 w-64 overflow-hidden rounded shadow-xl">
              <img
                src={`${active.album_image_url}?b=${myBracket}`}
                alt=""
                class="h-full w-full"
              />
            </div>
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
            <div class="flex items-center gap-2">
              <input
                class="input flex-1"
                placeholder="Search the song you think it is…"
                bind:value={query}
                oninput={onQueryInput}
                onkeydown={onSearchKeyDown}
                disabled={submitting}
              />
              <button
                type="button"
                class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-border text-sm font-semibold text-text-secondary hover:bg-surface-raised"
                onclick={() => (showMatchingHelp = !showMatchingHelp)}
                aria-label="How are guesses matched?"
                aria-expanded={showMatchingHelp}
              >
                ?
              </button>
            </div>
            {#if showMatchingHelp}
              <div
                class="rounded-md border border-border bg-surface-raised p-3 text-xs text-text-secondary"
              >
                <p class="mb-1 font-semibold text-text-primary">
                  How guesses are matched
                </p>
                <p>
                  A guess counts when both the <strong>title</strong> and
                  <strong>artist</strong> match the picked track. Comparison is
                  case-insensitive and ignores punctuation, so different
                  remasters / radio edits / live versions all count as the same
                  song. Parenthetical sections like
                  <em>(Remastered 2009)</em>
                  and <em>feat.</em> credits are stripped before comparing.
                  Different songs sharing a title (e.g. Adele's “Hello” vs.
                  Lionel Richie's) won't match because the artist differs.
                </p>
              </div>
            {/if}
            {#if searching}
              <p class="text-xs text-text-muted">searching…</p>
            {/if}
            {#if searchResults.length > 0}
              <ul
                bind:this={resultListEl}
                class="max-h-72 overflow-y-auto rounded-md border border-border bg-surface-raised"
              >
                {#each searchResults as r, i (r.spotify_track_id)}
                  <li>
                    <button
                      type="button"
                      class="flex w-full items-center gap-3 p-2 text-left hover:bg-surface disabled:opacity-50"
                      class:bg-surface={highlightedIndex === i}
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
