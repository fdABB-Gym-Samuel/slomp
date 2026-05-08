<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import type { PublicRoom } from '$lib/types';

  let joinCode = $state('');
  let creating = $state(false);
  let joining = $state(false);
  let error = $state<string | null>(null);

  let publicRooms = $state<PublicRoom[] | null>(null);
  let publicError = $state<string | null>(null);
  let publicLoading = $state(false);
  let joiningId = $state<string | null>(null);

  async function createRoom() {
    creating = true;
    error = null;
    try {
      const room = await api.createRoom();
      goto(`/room/${room.id}`);
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      creating = false;
    }
  }

  async function joinRoom(e: Event) {
    e.preventDefault();
    if (!joinCode.trim()) return;
    joining = true;
    error = null;
    const code = joinCode.trim().toUpperCase();
    try {
      const room = await api.joinByCode(code);
      goto(`/room/${room.id}`);
    } catch (err) {
      error = err instanceof APIError ? err.message : String(err);
    } finally {
      joining = false;
    }
  }

  async function loadPublicRooms() {
    if (!auth.user) return;
    publicLoading = true;
    publicError = null;
    try {
      publicRooms = await api.listPublicRooms();
    } catch (e) {
      publicError = e instanceof APIError ? e.message : String(e);
    } finally {
      publicLoading = false;
    }
  }

  async function joinPublic(id: string) {
    joiningId = id;
    publicError = null;
    try {
      await api.joinRoom(id);
      goto(`/room/${id}`);
    } catch (e) {
      publicError = e instanceof APIError ? e.message : String(e);
      joiningId = null;
    }
  }

  async function logout() {
    await auth.logout();
    goto('/login');
  }

  onMount(loadPublicRooms);
</script>

<div class="mx-auto max-w-2xl p-8">
  <header class="mb-12 flex items-baseline justify-between">
    <h1 class="text-4xl font-bold tracking-tight text-accent">slomp</h1>
    {#if auth.user}
      <div class="flex items-center gap-4 text-sm">
        <span class="text-text-secondary">
          signed in as <span class="text-text-primary">{auth.user.username}</span>
        </span>
        <button class="btn-ghost" onclick={logout}>Log out</button>
      </div>
    {/if}
  </header>

  <div class="grid gap-6 md:grid-cols-2">
    <div class="card">
      <h2 class="text-xl font-semibold">Create a room</h2>
      <p class="mt-1 mb-6 text-sm text-text-secondary">
        Start a new game and invite friends with the room code.
      </p>
      <button
        class="btn-primary w-full"
        onclick={createRoom}
        disabled={creating}
      >
        {creating ? 'Creating…' : 'Create room'}
      </button>
    </div>

    <div class="card">
      <h2 class="text-xl font-semibold">Join a room</h2>
      <p class="mt-1 mb-6 text-sm text-text-secondary">
        Got a code from a friend? Drop it in below.
      </p>
      <form class="space-y-3" onsubmit={joinRoom}>
        <input
          class="input uppercase tracking-widest"
          placeholder="ABC123"
          bind:value={joinCode}
          maxlength="8"
        />
        <button class="btn-secondary w-full" disabled={joining || !joinCode.trim()}>
          {joining ? 'Joining…' : 'Join'}
        </button>
      </form>
    </div>
  </div>

  {#if error}
    <p class="mt-6 text-sm text-danger">{error}</p>
  {/if}

  <section class="mt-12">
    <div class="mb-4 flex items-baseline justify-between">
      <h2 class="text-xl font-semibold">Public rooms</h2>
      <button
        type="button"
        class="btn-ghost text-xs"
        onclick={loadPublicRooms}
        disabled={publicLoading}
      >
        {publicLoading ? 'Refreshing…' : 'Refresh'}
      </button>
    </div>

    {#if publicError}
      <p class="mb-3 text-sm text-danger">{publicError}</p>
    {/if}

    {#if publicRooms === null && !publicError}
      <p class="text-sm text-text-muted">Loading…</p>
    {:else if publicRooms && publicRooms.length === 0}
      <div class="card text-center text-sm text-text-muted">
        No public rooms right now. Create one and toggle "Public" in the lobby
        to let others find it here.
      </div>
    {:else if publicRooms}
      <ul class="space-y-2">
        {#each publicRooms as room (room.id)}
          <li class="card flex items-center justify-between gap-4 py-4">
            <div class="min-w-0 flex-1">
              <p class="truncate font-medium text-text-primary">
                {room.name ?? 'Untitled room'}
              </p>
              <p class="mt-1 text-xs text-text-muted">
                hosted by <span class="text-text-secondary">{room.leader_username}</span>
                · {room.player_count}
                {room.player_count === 1 ? 'player' : 'players'}
                · {room.songs_per_player}
                {room.songs_per_player === 1 ? 'song' : 'songs'} each
              </p>
            </div>
            <button
              type="button"
              class="btn-secondary text-sm"
              disabled={joiningId !== null}
              onclick={() => joinPublic(room.id)}
            >
              {joiningId === room.id ? 'Joining…' : 'Join'}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</div>
