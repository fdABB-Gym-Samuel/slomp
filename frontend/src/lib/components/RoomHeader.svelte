<script lang="ts">
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import type { Room } from '$lib/types';

  let { roomData }: { roomData: Room } = $props();

  async function leave() {
    if (!confirm('Leave this room?')) return;
    try {
      await api.leaveRoom(roomData.id);
    } catch {}
    goto('/');
  }

  const phaseLabels: Record<Room['status'], string> = {
    lobby: 'Lobby',
    selecting: 'Choosing songs',
    playing: 'In game',
    results: 'Results',
  };
</script>

<header class="border-b border-border pb-4">
  <p class="text-sm text-text-muted">Room</p>
  <div class="mt-1 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      {#if roomData.name}
        <h1 class="text-2xl font-bold text-text-primary">
          {roomData.name}
          {#if roomData.code}
            <span class="ml-2 font-mono text-base font-normal tracking-widest text-accent">
              {roomData.code}
            </span>
          {/if}
        </h1>
      {:else if roomData.code}
        <h1 class="font-mono text-3xl font-bold tracking-widest text-accent">
          {roomData.code}
        </h1>
      {:else}
        <h1 class="text-2xl font-bold text-text-primary">Untitled room</h1>
      {/if}
      <div class="flex items-center gap-2 text-sm">
        {#if roomData.is_public}
          <span class="badge bg-success/15 text-success">Public</span>
        {/if}
        <span class="badge bg-surface-raised text-text-secondary">
          {phaseLabels[roomData.status]}
        </span>
      </div>
    </div>
    <button class="btn-secondary text-sm" onclick={leave}>Leave</button>
  </div>
</header>
