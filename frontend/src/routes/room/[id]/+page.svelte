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
      // Joining requires picking a name on the home page first. Forward
      // any share-link code so the home page can prefill the join modal.
      const code = $page.url.searchParams.get('code');
      goto(code ? `/?code=${encodeURIComponent(code)}` : '/');
      return;
    }
    const id = $page.params.id;
    if (!id) {
      connectError = 'missing room id';
      return;
    }
    try {
      const existing = await api.getRoom(id);
      const isMember = existing.players.some((p) => p.user.id === auth.user!.id);
      if (!isMember) {
        // The user has a session but isn't in this room. Send them home to
        // pick a name (they'd need a fresh identity per the per-room model).
        goto('/');
        return;
      }
      // Strip any leftover ?code= from the URL so it doesn't linger.
      if ($page.url.searchParams.has('code')) {
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
