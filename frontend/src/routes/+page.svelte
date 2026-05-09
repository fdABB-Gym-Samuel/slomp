<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { wsBase } from '$lib/url';
  import type { PublicRoom, RoomStatus } from '$lib/types';

  type ModalAction =
    | { kind: 'create' }
    | { kind: 'join_code'; code: string }
    | { kind: 'join_public'; id: string };

  const USERNAME_KEY = 'slomp:lastUsername';

  let joinCode = $state('');
  let error = $state<string | null>(null);

  let publicRooms = $state<PublicRoom[] | null>(null);
  let publicError = $state<string | null>(null);
  let publicLoading = $state(false);

  let modal = $state<ModalAction | null>(null);
  let modalUsername = $state('');
  let modalSubmitting = $state(false);
  let modalError = $state<string | null>(null);

  const STAGE_BADGE: Record<RoomStatus, { label: string; cls: string }> = {
    lobby: { label: 'lobby', cls: 'bg-success/20 text-success' },
    selecting: { label: 'picking', cls: 'bg-accent/20 text-accent' },
    playing: { label: 'playing', cls: 'bg-warning/20 text-warning' },
    results: { label: 'results', cls: 'bg-text-muted/20 text-text-secondary' },
  };

  // Tick `now` once a second so the public-rooms cleanup countdown updates.
  // Only runs while at least one room is in the cleanup window — otherwise
  // we'd re-render the page every second for nothing.
  let now = $state(Date.now() / 1000);
  $effect(() => {
    const needsTicker = (publicRooms ?? []).some((r) => r.cleanup_at !== null);
    if (!needsTicker) return;
    const id = setInterval(() => (now = Date.now() / 1000), 500);
    return () => clearInterval(id);
  });

  function cleanupSeconds(at: number): number {
    return Math.max(0, Math.ceil(at - now));
  }

  function openModal(action: ModalAction) {
    modal = action;
    modalUsername = localStorage.getItem(USERNAME_KEY) ?? '';
    modalError = null;
  }

  function closeModal() {
    modal = null;
    modalSubmitting = false;
    modalError = null;
  }

  async function submitModal(e: Event) {
    e.preventDefault();
    if (modal === null) return;
    const name = modalUsername.trim();
    if (name.length < 3 || name.length > 32) {
      modalError = 'name must be 3–32 characters';
      return;
    }
    modalSubmitting = true;
    modalError = null;
    try {
      let room;
      if (modal.kind === 'create') {
        room = await api.createRoom(name);
      } else if (modal.kind === 'join_code') {
        room = await api.joinByCode(modal.code, name);
      } else {
        room = await api.joinRoom(modal.id, name);
      }
      localStorage.setItem(USERNAME_KEY, name);
      // refresh /me so the auth store has the canonical id+username
      await auth.refresh();
      goto(`/room/${room.id}`);
    } catch (err) {
      modalError = err instanceof APIError ? err.message : String(err);
      modalSubmitting = false;
    }
  }

  function startCreate() {
    openModal({ kind: 'create' });
  }

  function startJoinByCode(e: Event) {
    e.preventDefault();
    const code = joinCode.trim().toUpperCase();
    if (!code) return;
    openModal({ kind: 'join_code', code });
  }

  function startJoinPublic(id: string) {
    openModal({ kind: 'join_public', id });
  }

  // Live updates over WS. The /lobby/ws endpoint sends an initial snapshot
  // and then `public_room_upsert` / `public_room_removed` deltas as rooms
  // change. We still keep `loadPublicRooms` as a manual REST fallback in
  // case the WS path is ever unavailable.
  let lobbyWs: WebSocket | null = null;
  let lobbyReconnect: ReturnType<typeof setTimeout> | null = null;
  let lobbyPing: ReturnType<typeof setInterval> | null = null;

  function openLobbyWs() {
    const ws = new WebSocket(`${wsBase()}/lobby/ws`);
    lobbyWs = ws;

    ws.onmessage = (e) => {
      try {
        handleLobbyEvent(JSON.parse(e.data));
      } catch (err) {
        console.error('lobby ws parse error', err);
      }
    };
    ws.onclose = () => {
      lobbyWs = null;
      if (lobbyPing) {
        clearInterval(lobbyPing);
        lobbyPing = null;
      }
      lobbyReconnect = setTimeout(openLobbyWs, 1500);
    };
    ws.onopen = () => {
      lobbyPing = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping', payload: {} }));
        }
      }, 25000);
    };
  }

  function closeLobbyWs() {
    if (lobbyReconnect) {
      clearTimeout(lobbyReconnect);
      lobbyReconnect = null;
    }
    if (lobbyPing) {
      clearInterval(lobbyPing);
      lobbyPing = null;
    }
    lobbyWs?.close();
    lobbyWs = null;
  }

  function handleLobbyEvent(ev: { type: string; payload: any }) {
    switch (ev.type) {
      case 'public_rooms_snapshot':
        publicRooms = ev.payload.rooms as PublicRoom[];
        publicError = null;
        break;
      case 'public_room_upsert': {
        const r = ev.payload as PublicRoom;
        const list = publicRooms ?? [];
        const idx = list.findIndex((x) => x.id === r.id);
        publicRooms =
          idx >= 0
            ? list.map((x, i) => (i === idx ? r : x))
            : [r, ...list];
        break;
      }
      case 'public_room_removed': {
        if (publicRooms === null) break;
        publicRooms = publicRooms.filter((r) => r.id !== ev.payload.id);
        break;
      }
    }
  }

  async function loadPublicRooms() {
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

  onMount(() => {
    openLobbyWs();
    // Share-link forwarding: /?code=ABC123 (set by the room page when an
    // anon visitor follows a share link) auto-opens the join modal.
    const url = new URL(location.href);
    const sharedCode = url.searchParams.get('code');
    if (sharedCode) {
      openModal({ kind: 'join_code', code: sharedCode.toUpperCase() });
      url.searchParams.delete('code');
      history.replaceState(history.state, '', url.toString());
    }
  });

  onDestroy(closeLobbyWs);
</script>

<div class="mx-auto max-w-2xl p-8">
  <header class="mb-12">
    <h1 class="text-4xl font-bold tracking-tight text-accent">slomp</h1>
  </header>

  <div class="grid gap-6 md:grid-cols-2">
    <div class="card">
      <h2 class="text-xl font-semibold">Create a room</h2>
      <p class="mt-1 mb-6 text-sm text-text-secondary">
        Start a new game and invite friends with the room code.
      </p>
      <button class="btn-primary w-full" onclick={startCreate}>
        Create room
      </button>
    </div>

    <div class="card">
      <h2 class="text-xl font-semibold">Join a room</h2>
      <p class="mt-1 mb-6 text-sm text-text-secondary">
        Got a code from a friend? Drop it in below.
      </p>
      <form class="space-y-3" onsubmit={startJoinByCode}>
        <input
          class="input uppercase tracking-widest"
          placeholder="ABC123"
          bind:value={joinCode}
          maxlength="8"
        />
        <button class="btn-secondary w-full" disabled={!joinCode.trim()}>
          Join
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
              <div class="flex items-center gap-2">
                <p class="truncate font-medium text-text-primary">
                  {room.name ?? 'Untitled room'}
                </p>
                <span class="badge {STAGE_BADGE[room.status].cls}">
                  {STAGE_BADGE[room.status].label}
                </span>
                {#if room.game_mode === 'random'}
                  <span class="badge bg-accent/20 text-accent">random</span>
                {:else}
                  <span class="badge bg-text-muted/20 text-text-secondary">classic</span>
                {/if}
              </div>
              <p class="mt-1 text-xs text-text-muted">
                hosted by <span class="text-text-secondary">{room.leader_username}</span>
                · {room.player_count}
                {room.player_count === 1 ? 'player' : 'players'}
                {#if room.game_mode === 'random'}
                  · {room.random_song_count}
                  {room.random_song_count === 1 ? 'song' : 'songs'}
                {:else}
                  · {room.songs_per_player}
                  {room.songs_per_player === 1 ? 'song' : 'songs'} each
                {/if}
              </p>
              {#if room.cleanup_at}
                <p class="mt-1 text-xs text-danger">
                  empty — closes in {cleanupSeconds(room.cleanup_at)}s unless someone joins
                </p>
              {/if}
              {#if room.joins_as_spectator}
                <p class="mt-1 text-xs text-text-muted">
                  game in progress — you'll spectate until the next game
                </p>
              {/if}
            </div>
            <button
              type="button"
              class="btn-secondary text-sm"
              onclick={() => startJoinPublic(room.id)}
            >
              {room.joins_as_spectator ? 'Spectate' : 'Join'}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</div>

{#if modal !== null}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    role="dialog"
    aria-modal="true"
  >
    <div class="card w-full max-w-sm">
      <h2 class="mb-2 text-lg font-semibold">Pick a name</h2>
      <p class="mb-4 text-sm text-text-secondary">
        This is how other players will see you in the room.
      </p>
      <form onsubmit={submitModal} class="space-y-3">
        <!-- svelte-ignore a11y_autofocus -->
        <input
          class="input"
          bind:value={modalUsername}
          placeholder="your name"
          minlength="3"
          maxlength="32"
          autocomplete="off"
          autofocus
        />
        {#if modalError}
          <p class="text-sm text-danger">{modalError}</p>
        {/if}
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="btn-ghost"
            onclick={closeModal}
            disabled={modalSubmitting}
          >
            Cancel
          </button>
          <button
            class="btn-primary"
            disabled={modalSubmitting || !modalUsername.trim()}
          >
            {modalSubmitting ? 'Joining…' : 'Continue'}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}
