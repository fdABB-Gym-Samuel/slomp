<script lang="ts">
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { room } from '$lib/ws.svelte';
  import type { Room, ScoreboardEntry } from '$lib/types';

  let { roomData }: { roomData: Room } = $props();

  let scoreboard = $state<ScoreboardEntry[] | null>(null);
  let restarting = $state(false);
  let error = $state<string | null>(null);

  const isLeader = $derived(auth.user?.id === roomData.leader_id);

  async function load() {
    try {
      scoreboard = await api.results(roomData.code);
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    }
  }

  $effect(() => {
    if (room.finalScoreboard) {
      scoreboard = room.finalScoreboard;
    } else {
      load();
    }
  });

  async function restart() {
    restarting = true;
    error = null;
    try {
      await api.restart(roomData.code);
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      restarting = false;
    }
  }

  const medals = ['🥇', '🥈', '🥉'];
</script>

<div class="mx-auto max-w-2xl">
  <div class="card">
    <h2 class="text-center text-2xl font-bold text-accent">Final results</h2>
    {#if !scoreboard}
      <p class="mt-6 text-center text-text-muted">Loading…</p>
    {:else}
      <ol class="mt-8 space-y-2">
        {#each scoreboard as entry, i (entry.user.id)}
          <li
            class="flex items-center justify-between rounded-md border border-border p-3"
            class:bg-secondary={i === 0}
            class:text-bg={i === 0}
          >
            <span class="flex items-center gap-3">
              <span class="text-xl">{medals[i] ?? `#${i + 1}`}</span>
              <span class="font-medium">{entry.user.username}</span>
            </span>
            <span class="font-mono text-lg font-bold">{entry.score}</span>
          </li>
        {/each}
      </ol>
    {/if}

    {#if error}
      <p class="mt-4 text-sm text-danger">{error}</p>
    {/if}

    {#if isLeader}
      <button
        class="btn-secondary mt-8 w-full"
        onclick={restart}
        disabled={restarting}
      >
        {restarting ? 'Resetting…' : 'Play again (back to lobby)'}
      </button>
    {:else}
      <p class="mt-8 text-center text-sm text-text-muted">
        Waiting for the leader to start a new round…
      </p>
    {/if}
  </div>
</div>
