<script lang="ts">
  import type { RoomPlayer } from '$lib/types';

  let {
    players,
    leaderId,
    songsPerPlayer,
    showSubmissions = false,
    highlightUserId = null,
  }: {
    players: RoomPlayer[];
    leaderId: string;
    songsPerPlayer?: number;
    showSubmissions?: boolean;
    highlightUserId?: string | null;
  } = $props();
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
      </div>
      <div class="flex items-center gap-3 text-sm text-text-secondary">
        {#if showSubmissions && songsPerPlayer}
          <span class:text-success={p.songs_submitted >= songsPerPlayer}>
            {p.songs_submitted} / {songsPerPlayer}
          </span>
        {:else}
          <span>{p.score} pts</span>
        {/if}
      </div>
    </li>
  {/each}
</ul>
