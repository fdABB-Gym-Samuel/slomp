<script lang="ts">
  import type { RoomPlayer } from '$lib/types';

  let {
    players,
    leaderId,
    songsPerPlayer,
    showSubmissions = false,
    highlightUserId = null,
    showLeaderActions = false,
    onPromote,
    onKick,
  }: {
    players: RoomPlayer[];
    leaderId: string;
    songsPerPlayer?: number;
    showSubmissions?: boolean;
    highlightUserId?: string | null;
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
</script>

<ul class="divide-y divide-border">
  {#each players as p (p.user.id)}
    <li class="flex items-center justify-between py-2">
      <div class="flex items-center gap-3">
        <span
          class="h-2 w-2 rounded-full {p.connected ? 'bg-success' : 'bg-text-muted'}"
          title={p.connected ? 'connected' : 'disconnected'}
        ></span>
        <span
          class="font-medium"
          class:text-secondary={p.user.id === highlightUserId}
        >
          {p.user.username}
        </span>
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
