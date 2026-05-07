<script lang="ts">
  import { goto } from '$app/navigation';
  import { api } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import type { Room } from '$lib/types';

  let { roomData }: { roomData: Room } = $props();

  async function leave() {
    if (!confirm('Leave this room?')) return;
    try {
      await api.leaveRoom(roomData.code);
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

<header class="flex items-baseline justify-between border-b border-border pb-4">
  <div>
    <p class="text-sm text-text-muted">Room code</p>
    <h1 class="font-mono text-3xl font-bold tracking-widest text-accent">
      {roomData.code}
    </h1>
  </div>
  <div class="flex items-center gap-4 text-sm">
    <span class="badge bg-surface-raised text-text-secondary">
      {phaseLabels[roomData.status]}
    </span>
    {#if auth.user}
      <span class="text-text-secondary">
        {auth.user.username}
      </span>
    {/if}
    <button class="btn-ghost text-sm" onclick={leave}>Leave</button>
  </div>
</header>
