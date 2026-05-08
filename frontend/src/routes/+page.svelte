<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import type { MyRoom, PublicRoom, RoomStatus } from '$lib/types';

  let joinCode = $state('');
  let creating = $state(false);
  let joining = $state(false);
  let error = $state<string | null>(null);

  let publicRooms = $state<PublicRoom[] | null>(null);
  let publicError = $state<string | null>(null);
  let publicLoading = $state(false);
  let joiningId = $state<string | null>(null);

  let myRooms = $state<MyRoom[]>([]);
  let myRoomsError = $state<string | null>(null);
  let leavingId = $state<string | null>(null);

  const STATUS_LABEL: Record<MyRoom['status'], string> = {
    lobby: 'in the lobby',
    selecting: 'picking songs',
    playing: 'playing now',
    results: 'showing results',
  };

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

  // Live updates over WS. The /lobby/ws endpoint sends an initial snapshot
  // and then `public_room_upsert` / `public_room_removed` deltas as rooms
  // change. We still keep `loadPublicRooms` as a manual REST fallback in
  // case the WS path is ever unavailable.
  let lobbyWs: WebSocket | null = null;
  let lobbyReconnect: ReturnType<typeof setTimeout> | null = null;
  let lobbyPing: ReturnType<typeof setInterval> | null = null;

  function openLobbyWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsHost =
      location.port === '5173' ? `${location.hostname}:8000` : location.host;
    const url = `${proto}//${wsHost}/lobby/ws`;
    const ws = new WebSocket(url);
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

  async function loadMyRooms() {
    if (!auth.user) return;
    myRoomsError = null;
    try {
      myRooms = await api.listMyRooms();
    } catch (e) {
      myRoomsError = e instanceof APIError ? e.message : String(e);
    }
  }

  async function leaveExisting(id: string) {
    leavingId = id;
    myRoomsError = null;
    try {
      await api.leaveRoom(id);
      myRooms = myRooms.filter((r) => r.id !== id);
    } catch (e) {
      myRoomsError = e instanceof APIError ? e.message : String(e);
    } finally {
      leavingId = null;
    }
  }

  onMount(() => {
    loadMyRooms();
    if (auth.user) openLobbyWs();
  });

  onDestroy(closeLobbyWs);
</script>

<div class="mx-auto max-w-2xl p-8">
  <header class="mb-12">
    <h1 class="text-4xl font-bold tracking-tight text-accent">slomp</h1>
  </header>

  {#if myRooms.length > 0}
    <section class="mb-8 space-y-3">
      {#each myRooms as r (r.id)}
        <div class="card flex flex-wrap items-center justify-between gap-3 border-accent/40">
          <div class="min-w-0 flex-1">
            <p class="text-sm text-text-muted">You're already in a room</p>
            <p class="truncate font-medium text-text-primary">
              {r.name ?? 'Untitled room'}
              <span class="ml-1 text-xs text-text-muted">— {STATUS_LABEL[r.status]}</span>
            </p>
          </div>
          <div class="flex gap-2">
            <button
              type="button"
              class="btn-primary text-sm"
              onclick={() => goto(`/room/${r.id}`)}
            >
              Go to room
            </button>
            <button
              type="button"
              class="btn-ghost text-sm"
              disabled={leavingId === r.id}
              onclick={() => leaveExisting(r.id)}
            >
              {leavingId === r.id ? 'Leaving…' : 'Leave'}
            </button>
          </div>
        </div>
      {/each}
      {#if myRoomsError}
        <p class="text-sm text-danger">{myRoomsError}</p>
      {/if}
    </section>
  {/if}

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
              <div class="flex items-center gap-2">
                <p class="truncate font-medium text-text-primary">
                  {room.name ?? 'Untitled room'}
                </p>
                <span class="badge {STAGE_BADGE[room.status].cls}">
                  {STAGE_BADGE[room.status].label}
                </span>
              </div>
              <p class="mt-1 text-xs text-text-muted">
                hosted by <span class="text-text-secondary">{room.leader_username}</span>
                · {room.player_count}
                {room.player_count === 1 ? 'player' : 'players'}
                · {room.songs_per_player}
                {room.songs_per_player === 1 ? 'song' : 'songs'} each
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
              disabled={joiningId !== null}
              onclick={() => joinPublic(room.id)}
            >
              {joiningId === room.id
                ? 'Joining…'
                : room.joins_as_spectator
                  ? 'Spectate'
                  : 'Join'}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</div>
