<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { room } from '$lib/ws.svelte';
  import RoomHeader from '$lib/components/RoomHeader.svelte';
  import LobbyPhase from '$lib/components/LobbyPhase.svelte';
  import SelectingPhase from '$lib/components/SelectingPhase.svelte';
  import PlayingPhase from '$lib/components/PlayingPhase.svelte';
  import ResultsPhase from '$lib/components/ResultsPhase.svelte';

  let connectError = $state<string | null>(null);

  onMount(async () => {
    if (!auth.user) {
      goto('/login');
      return;
    }
    const id = $page.params.id;
    if (!id) {
      connectError = 'missing room id';
      return;
    }
    const code = $page.url.searchParams.get('code');
    try {
      const existing = await api.getRoom(id);
      const isMember = existing.players.some((p) => p.user.id === auth.user!.id);
      if (!isMember) {
        // Private rooms only accept join-by-id from existing members; use the
        // code from the share link to admit a first-time joiner.
        if (code) {
          await api.joinByCode(code);
        } else {
          await api.joinRoom(id);
        }
      }
      if (code) {
        // Strip the code from the URL so it doesn't linger in history/refreshes.
        const cleanUrl = `${$page.url.pathname}${$page.url.hash}`;
        history.replaceState(history.state, '', cleanUrl);
      }
      room.connect(id);
    } catch (e) {
      connectError = e instanceof APIError ? e.message : String(e);
    }
  });

  onDestroy(() => {
    room.disconnect();
  });
</script>

<div class="mx-auto max-w-5xl space-y-6 p-6 md:p-10">
  {#if room.kickedSelf}
    <div class="card space-y-4">
      <h2 class="text-lg font-semibold">You were removed from the room</h2>
      <p class="text-sm text-text-secondary">
        The room leader kicked you. You can join another room from the home page.
      </p>
      <button class="btn-primary" onclick={() => goto('/')}>Back to home</button>
    </div>
  {:else if connectError}
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
