<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { room } from '$lib/ws.svelte';
  import RoomHeader from '$lib/components/RoomHeader.svelte';
  import LobbyPhase from '$lib/components/LobbyPhase.svelte';
  import SelectingPhase from '$lib/components/SelectingPhase.svelte';
  import PlayingPhase from '$lib/components/PlayingPhase.svelte';
  import ResultsPhase from '$lib/components/ResultsPhase.svelte';

  let connectError = $state<string | null>(null);

  onMount(() => {
    if (!auth.user) {
      goto('/login');
      return;
    }
    const code = $page.params.code;
    if (!code) {
      connectError = 'missing room code';
      return;
    }
    try {
      room.connect(code);
    } catch (e) {
      connectError = e instanceof APIError ? e.message : String(e);
    }
  });

  onDestroy(() => {
    room.disconnect();
  });
</script>

<div class="mx-auto max-w-5xl space-y-6 p-6 md:p-10">
  {#if connectError}
    <p class="text-danger">{connectError}</p>
  {:else if !room.room}
    <p class="text-text-muted">Connecting to room…</p>
  {:else}
    <RoomHeader roomData={room.room} />

    {#if room.room.status === 'lobby'}
      <LobbyPhase roomData={room.room} />
    {:else if room.room.status === 'selecting'}
      <SelectingPhase roomData={room.room} />
    {:else if room.room.status === 'playing'}
      <PlayingPhase roomData={room.room} />
    {:else if room.room.status === 'results'}
      <ResultsPhase roomData={room.room} />
    {/if}
  {/if}
</div>
