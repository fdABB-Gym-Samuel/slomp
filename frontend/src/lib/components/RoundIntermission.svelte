<script lang="ts">
  import { onDestroy } from 'svelte';
  import { flip } from 'svelte/animate';
  import { fade } from 'svelte/transition';
  import type { ScoreboardEntry } from '$lib/types';

  let {
    result,
    meId,
  }: {
    result: {
      title: string;
      artist: string;
      album_image_url: string | null;
      full_audio_url: string | null;
      scoreboard: ScoreboardEntry[];
      intermission_ends_at: number;
      is_last_round: boolean;
    };
    meId: string;
  } = $props();

  // Hold the previous-ranked list briefly, then reorder to the new ranks so
  // FLIP can animate the rows sliding into their new positions.
  let showNew = $state(false);
  let now = $state(Date.now());
  let audio: HTMLAudioElement | null = $state(null);
  let audioBlocked = $state(false);

  const reorderDelayMs = 900;
  const reorderTimer = setTimeout(() => {
    showNew = true;
  }, reorderDelayMs);

  const tick = setInterval(() => {
    now = Date.now();
  }, 100);

  onDestroy(() => {
    clearTimeout(reorderTimer);
    clearInterval(tick);
    if (audio) {
      audio.pause();
      audio.src = '';
    }
  });

  $effect(() => {
    if (!audio || !result.full_audio_url) return;
    audio.src = result.full_audio_url;
    audio.play().catch(() => {
      // Autoplay blocked (e.g. first interaction not yet); show manual play.
      audioBlocked = true;
    });
  });

  async function manualPlay() {
    if (!audio) return;
    try {
      await audio.play();
      audioBlocked = false;
    } catch {
      audioBlocked = true;
    }
  }

  // Build entries with previous and new ranks so we can show movement arrows.
  type Row = ScoreboardEntry & {
    prev: number;
    delta: number;
    prevRank: number;
    newRank: number;
  };

  const rows: Row[] = $derived.by(() => {
    const entries = result.scoreboard;
    const byPrev = [...entries].sort((a, b) => {
      const pa = a.previous_score ?? a.score;
      const pb = b.previous_score ?? b.score;
      return pb - pa || a.user.username.localeCompare(b.user.username);
    });
    const byNew = [...entries].sort(
      (a, b) =>
        b.score - a.score || a.user.username.localeCompare(b.user.username)
    );
    const prevRankMap = new Map(byPrev.map((e, i) => [e.user.id, i]));
    const newRankMap = new Map(byNew.map((e, i) => [e.user.id, i]));
    return entries.map((e) => {
      const prev = e.previous_score ?? e.score;
      return {
        ...e,
        prev,
        delta: e.score - prev,
        prevRank: prevRankMap.get(e.user.id) ?? 0,
        newRank: newRankMap.get(e.user.id) ?? 0,
      };
    });
  });

  const sortedRows: Row[] = $derived.by(() => {
    const key: (r: Row) => number = showNew
      ? (r) => r.newRank
      : (r) => r.prevRank;
    return [...rows].sort((a, b) => key(a) - key(b));
  });

  const secondsLeft = $derived(
    Math.max(0, Math.ceil((result.intermission_ends_at - now) / 1000))
  );
</script>

<div class="space-y-6">
  <div class="card flex flex-col items-center gap-3 text-center">
    <p class="text-sm uppercase tracking-wide text-text-muted">Round over</p>
    {#if result.album_image_url}
      <img
        src={result.album_image_url}
        alt=""
        class="h-40 w-40 rounded shadow-xl"
      />
    {/if}
    <h2 class="text-2xl font-semibold">{result.title}</h2>
    <p class="text-text-secondary">{result.artist}</p>

    {#if result.full_audio_url}
      <audio bind:this={audio} preload="auto"></audio>
      {#if audioBlocked}
        <button class="btn-ghost mt-2 text-sm" type="button" onclick={manualPlay}>
          ▶ Play preview
        </button>
      {/if}
    {/if}

    {#if !result.is_last_round}
      <p class="mt-2 text-sm text-text-muted">
        Next round in {secondsLeft}s…
      </p>
    {:else}
      <p class="mt-2 text-sm text-text-muted">Final results coming up…</p>
    {/if}
  </div>

  <div class="card">
    <h3 class="mb-3 text-lg font-semibold">Standings</h3>
    <ul class="space-y-2">
      {#each sortedRows as r (r.user.id)}
        {@const rankShift = r.prevRank - r.newRank}
        {@const isMe = r.user.id === meId}
        <li
          animate:flip={{ duration: 600 }}
          class="flex items-center justify-between rounded-md border border-border p-3"
          class:bg-surface-raised={isMe}
        >
          <span class="flex items-center gap-3">
            <span class="w-6 text-right font-mono text-text-muted">
              {(showNew ? r.newRank : r.prevRank) + 1}
            </span>
            <span class="font-medium" class:text-secondary={isMe}>
              {r.user.username}
            </span>
            {#if showNew && rankShift !== 0}
              <span
                in:fade={{ duration: 300 }}
                class="text-xs"
                class:text-success={rankShift > 0}
                class:text-warning={rankShift < 0}
              >
                {rankShift > 0 ? '▲' : '▼'}{Math.abs(rankShift)}
              </span>
            {/if}
          </span>
          <span class="flex items-center gap-3">
            {#if r.delta > 0}
              <span
                in:fade={{ duration: 300 }}
                class="text-xs font-medium text-success"
              >
                +{r.delta}
              </span>
            {/if}
            <span class="font-mono text-lg font-bold">
              {showNew ? r.score : r.prev}
            </span>
          </span>
        </li>
      {/each}
    </ul>
  </div>
</div>
