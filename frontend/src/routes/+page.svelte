<script lang="ts">
  import { goto } from '$app/navigation';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';

  let joinCode = $state('');
  let creating = $state(false);
  let joining = $state(false);
  let error = $state<string | null>(null);

  async function createRoom() {
    creating = true;
    error = null;
    try {
      const room = await api.createRoom();
      goto(`/room/${room.code}`);
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
      await api.joinRoom(code);
      goto(`/room/${code}`);
    } catch (err) {
      error = err instanceof APIError ? err.message : String(err);
    } finally {
      joining = false;
    }
  }

  async function logout() {
    await auth.logout();
    goto('/login');
  }
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
</div>
