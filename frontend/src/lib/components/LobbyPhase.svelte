<script lang="ts">
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import type { Room } from '$lib/types';
  import PlayerList from './PlayerList.svelte';
  import SettingsPanel from './SettingsPanel.svelte';

  let { roomData }: { roomData: Room } = $props();

  let starting = $state(false);
  let error = $state<string | null>(null);

  const isLeader = $derived(auth.user?.id === roomData.leader_id);

  async function startSelecting() {
    starting = true;
    error = null;
    try {
      await api.changePhase(roomData.code, 'selecting');
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      starting = false;
    }
  }
</script>

<div class="grid gap-6 md:grid-cols-[1fr_2fr]">
  <div class="card">
    <h2 class="mb-3 text-lg font-semibold">
      Players <span class="text-text-muted">({roomData.players.length})</span>
    </h2>
    <PlayerList
      players={roomData.players}
      leaderId={roomData.leader_id}
      highlightUserId={auth.user?.id ?? null}
    />
    <p class="mt-4 text-sm text-text-secondary">
      Share the code <span class="font-mono text-accent">{roomData.code}</span> to invite friends.
    </p>
  </div>

  <div class="space-y-4">
    {#if isLeader}
      <SettingsPanel {roomData} />
      {#if error}
        <p class="text-sm text-danger">{error}</p>
      {/if}
      <button class="btn-primary w-full" disabled={starting} onclick={startSelecting}>
        {starting ? 'Starting…' : 'Start song selection'}
      </button>
    {:else}
      <div class="card">
        <h2 class="text-lg font-semibold">Waiting for the leader</h2>
        <p class="mt-2 text-sm text-text-secondary">
          The leader configures the rules and starts the round.
        </p>
        <dl class="mt-6 grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt class="text-text-muted">Songs per player</dt>
            <dd class="font-medium">{roomData.settings.songs_per_player}</dd>
          </div>
          <div>
            <dt class="text-text-muted">Min popularity</dt>
            <dd class="font-medium">{roomData.settings.min_popularity}</dd>
          </div>
          <div class="col-span-2">
            <dt class="text-text-muted">Guess brackets</dt>
            <dd class="font-mono">
              {roomData.settings.guess_brackets_seconds.join(', ')}s
            </dd>
          </div>
        </dl>
      </div>
    {/if}
  </div>
</div>
