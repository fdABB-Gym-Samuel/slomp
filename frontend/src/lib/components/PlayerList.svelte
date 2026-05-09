<script lang="ts">
  import { api, APIError } from '$lib/api';
  import type { RoomPlayer } from '$lib/types';

  let {
    players,
    leaderId,
    songsPerPlayer,
    showSubmissions = false,
    highlightUserId = null,
    roomId = null,
    showLeaderActions = false,
    onPromote,
    onKick,
  }: {
    players: RoomPlayer[];
    leaderId: string | null;
    songsPerPlayer?: number;
    showSubmissions?: boolean;
    highlightUserId?: string | null;
    roomId?: string | null;
    showLeaderActions?: boolean;
    onPromote?: (userId: string, username: string) => void;
    onKick?: (userId: string, username: string) => void;
  } = $props();

  // Tick once a second so the displayed countdown stays current. Only runs
  // while a deadline exists, to avoid invalidating the component every
  // second when nobody is disconnected.
  let now = $state(Date.now() / 1000);
  $effect(() => {
    const needsTicker = players.some((p) => p.auto_leave_at != null);
    if (!needsTicker) return;
    const id = setInterval(() => (now = Date.now() / 1000), 500);
    return () => clearInterval(id);
  });

  function secondsUntil(deadline: number): number {
    return Math.max(0, Math.ceil(deadline - now));
  }

  let editing = $state(false);
  let draftName = $state('');
  let renameError = $state<string | null>(null);
  let renameSubmitting = $state(false);

  function startEdit(currentName: string) {
    draftName = currentName;
    renameError = null;
    editing = true;
  }

  function cancelEdit() {
    editing = false;
    renameError = null;
  }

  async function submitRename(e: Event) {
    e.preventDefault();
    if (!roomId) return;
    const name = draftName.trim();
    if (name.length < 3 || name.length > 32) {
      renameError = '3–32 characters';
      return;
    }
    renameSubmitting = true;
    renameError = null;
    try {
      await api.renameInRoom(roomId, name);
      editing = false;
    } catch (err) {
      renameError = err instanceof APIError ? err.message : String(err);
    } finally {
      renameSubmitting = false;
    }
  }
</script>

<ul class="divide-y divide-border">
  {#each players as p (p.user.id)}
    <li class="flex items-center justify-between py-2">
      <div class="flex items-center gap-3">
        <span
          class="h-2 w-2 rounded-full {p.connected ? 'bg-success' : 'bg-text-muted'}"
          title={p.connected ? 'connected' : 'disconnected'}
        ></span>
        {#if p.user.id === highlightUserId && editing && roomId}
          <form onsubmit={submitRename} class="flex items-center gap-2">
            <!-- svelte-ignore a11y_autofocus -->
            <input
              class="input h-7 px-2 py-0 text-sm"
              bind:value={draftName}
              minlength="3"
              maxlength="32"
              autocomplete="off"
              autofocus
              onkeydown={(e) => {
                if (e.key === 'Escape') cancelEdit();
              }}
            />
            <button
              type="submit"
              class="btn-ghost px-2 py-0 text-xs"
              disabled={renameSubmitting || !draftName.trim()}
              title="Save"
            >
              Save
            </button>
            <button
              type="button"
              class="btn-ghost px-2 py-0 text-xs"
              onclick={cancelEdit}
              disabled={renameSubmitting}
              title="Cancel"
            >
              Cancel
            </button>
            {#if renameError}
              <span class="text-xs text-danger">{renameError}</span>
            {/if}
          </form>
        {:else}
          <span
            class="font-medium"
            class:text-secondary={p.user.id === highlightUserId}
          >
            {p.user.username}
          </span>
          {#if p.user.id === highlightUserId && roomId}
            <button
              type="button"
              class="btn-ghost px-1 py-0 text-xs"
              title="Change name"
              onclick={() => startEdit(p.user.username)}
            >
              edit
            </button>
          {/if}
        {/if}
        {#if p.user.id === leaderId}
          <span class="badge bg-accent/20 text-accent">leader</span>
        {/if}
        {#if p.spectating}
          <span
            class="badge bg-text-muted/20 text-text-secondary"
            title="joined mid-game; sits out until the next game"
          >
            spectator
          </span>
        {/if}
        {#if !p.connected && p.auto_leave_at}
          <span
            class="text-xs text-danger"
            title="auto-leaves the room when this hits 0"
          >
            kick in {secondsUntil(p.auto_leave_at)}s
          </span>
        {/if}
      </div>
      <div class="flex items-center gap-3 text-sm text-text-secondary">
        {#if showSubmissions && songsPerPlayer}
          <span class:text-success={p.songs_submitted >= songsPerPlayer}>
            {p.songs_submitted} / {songsPerPlayer}
          </span>
        {:else}
          <span>{p.score} pts</span>
        {/if}
        {#if showLeaderActions && p.user.id !== leaderId}
          {#if onPromote}
            <button
              type="button"
              class="btn-ghost text-xs"
              title="Make leader"
              onclick={() => onPromote?.(p.user.id, p.user.username)}
            >
              Promote
            </button>
          {/if}
          {#if onKick}
            <button
              type="button"
              class="btn-ghost text-xs text-danger"
              title="Kick from room"
              onclick={() => onKick?.(p.user.id, p.user.username)}
            >
              Kick
            </button>
          {/if}
        {/if}
      </div>
    </li>
  {/each}
</ul>
