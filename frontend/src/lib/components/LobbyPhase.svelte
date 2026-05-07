<script lang="ts">
  import { page } from '$app/stores';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import type { Room } from '$lib/types';
  import PlayerList from './PlayerList.svelte';
  import SettingsPanel from './SettingsPanel.svelte';

  let { roomData }: { roomData: Room } = $props();

  type Tab = 'players' | 'settings' | 'start';
  let activeTab = $state<Tab>('players');

  let starting = $state(false);
  let startError = $state<string | null>(null);
  let playerError = $state<string | null>(null);
  let copyHint = $state<string | null>(null);

  const isLeader = $derived(auth.user?.id === roomData.leader_id);
  const shareLink = $derived(`${$page.url.origin}/room/${roomData.code}`);

  async function startSelecting() {
    starting = true;
    startError = null;
    try {
      await api.changePhase(roomData.code, 'selecting');
    } catch (e) {
      startError = e instanceof APIError ? e.message : String(e);
    } finally {
      starting = false;
    }
  }

  async function promote(userId: string, username: string) {
    if (!confirm(`Make ${username} the leader?`)) return;
    playerError = null;
    try {
      await api.promotePlayer(roomData.code, userId);
    } catch (e) {
      playerError = e instanceof APIError ? e.message : String(e);
    }
  }

  async function kick(userId: string, username: string) {
    if (!confirm(`Kick ${username} from the room?`)) return;
    playerError = null;
    try {
      await api.kickPlayer(roomData.code, userId);
    } catch (e) {
      playerError = e instanceof APIError ? e.message : String(e);
    }
  }

  async function copy(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      copyHint = `${label} copied`;
      setTimeout(() => (copyHint = null), 1500);
    } catch {
      copyHint = 'copy failed';
      setTimeout(() => (copyHint = null), 1500);
    }
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'players', label: 'Players' },
    { id: 'settings', label: 'Settings' },
    { id: 'start', label: 'Start' },
  ];
</script>

<div class="space-y-6">
  <nav class="flex gap-2 border-b border-border">
    {#each tabs as tab (tab.id)}
      <button
        type="button"
        class="border-b-2 px-4 py-2 text-sm font-medium transition-colors {activeTab ===
        tab.id
          ? 'border-accent text-accent'
          : 'border-transparent text-text-secondary hover:text-text-primary'}"
        onclick={() => (activeTab = tab.id)}
      >
        {tab.label}
        {#if tab.id === 'players'}
          <span class="ml-1 text-xs text-text-muted">({roomData.players.length})</span>
        {/if}
      </button>
    {/each}
  </nav>

  {#if activeTab === 'players'}
    <div class="grid gap-6 md:grid-cols-[2fr_1fr]">
      <div class="card">
        <h2 class="mb-3 text-lg font-semibold">
          Players <span class="text-text-muted">({roomData.players.length})</span>
        </h2>
        <PlayerList
          players={roomData.players}
          leaderId={roomData.leader_id}
          highlightUserId={auth.user?.id ?? null}
          showLeaderActions={isLeader}
          onPromote={promote}
          onKick={kick}
        />
        {#if playerError}
          <p class="mt-3 text-sm text-danger">{playerError}</p>
        {/if}
      </div>

      <div class="card space-y-4">
        <div>
          <p class="text-sm text-text-muted">Room code</p>
          <div class="mt-1 flex items-center gap-2">
            <span class="font-mono text-2xl font-bold tracking-widest text-accent">
              {roomData.code}
            </span>
            <button
              type="button"
              class="btn-ghost text-xs"
              onclick={() => copy(roomData.code, 'Code')}
            >
              Copy
            </button>
          </div>
        </div>
        <div>
          <p class="text-sm text-text-muted">Invite link (one-click join)</p>
          <div class="mt-1 flex items-center gap-2">
            <input
              class="input flex-1 truncate font-mono text-xs"
              readonly
              value={shareLink}
              onclick={(e) => e.currentTarget.select()}
            />
            <button
              type="button"
              class="btn-secondary text-xs"
              onclick={() => copy(shareLink, 'Link')}
            >
              Copy
            </button>
          </div>
          <p class="mt-2 text-xs text-text-muted">
            Friends opening this link will be added to the room automatically.
          </p>
        </div>
        {#if copyHint}
          <p class="text-xs text-success">{copyHint}</p>
        {/if}
      </div>
    </div>
  {:else if activeTab === 'settings'}
    <SettingsPanel roomData={roomData} readonly={!isLeader} />
  {:else if activeTab === 'start'}
    <div class="card space-y-4">
      <h2 class="text-lg font-semibold">Start the game</h2>
      {#if isLeader}
        <p class="text-sm text-text-secondary">
          Once you start, players will pick their songs in the next phase. Make
          sure your settings look right under the Settings tab.
        </p>
        {#if startError}
          <p class="text-sm text-danger">{startError}</p>
        {/if}
        <button
          class="btn-primary w-full"
          disabled={starting || roomData.players.length === 0}
          onclick={startSelecting}
        >
          {starting ? 'Starting…' : 'Start song selection'}
        </button>
      {:else}
        <p class="text-sm text-text-secondary">
          Waiting for the leader to start the round. They control when the game
          begins.
        </p>
      {/if}
    </div>
  {/if}
</div>
