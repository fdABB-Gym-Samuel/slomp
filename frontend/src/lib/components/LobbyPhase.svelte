<script lang="ts">
  import { page } from '$app/stores';
  import { api, APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';
  import { confirmDialog } from '$lib/confirm.svelte';
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
  let settingsDirty = $state(false);

  let nameDraft = $state('');
  let nameSaving = $state(false);
  let nameError = $state<string | null>(null);
  let visibilitySaving = $state(false);
  let visibilityError = $state<string | null>(null);

  const isLeader = $derived(auth.user?.id === roomData.leader_id);
  const isRandom = $derived(roomData.settings.game_mode === 'random');
  const shareLink = $derived(
    roomData.code
      ? `${$page.url.origin}/room/${roomData.id}?code=${roomData.code}`
      : `${$page.url.origin}/room/${roomData.id}`,
  );

  $effect(() => {
    // Sync local draft when server-side name changes (and we aren't editing).
    if (document.activeElement?.id !== 'room-name-input') {
      nameDraft = roomData.name ?? '';
    }
  });

  async function saveName() {
    if ((roomData.name ?? '') === nameDraft.trim()) return;
    nameSaving = true;
    nameError = null;
    try {
      await api.updateRoomInfo(roomData.id, { name: nameDraft.trim() });
    } catch (e) {
      nameError = e instanceof APIError ? e.message : String(e);
    } finally {
      nameSaving = false;
    }
  }

  async function toggleVisibility(e: Event) {
    const next = (e.currentTarget as HTMLInputElement).checked;
    visibilitySaving = true;
    visibilityError = null;
    try {
      await api.updateRoomInfo(roomData.id, { is_public: next });
    } catch (err) {
      visibilityError = err instanceof APIError ? err.message : String(err);
      // Revert checkbox state on failure
      (e.currentTarget as HTMLInputElement).checked = !next;
    } finally {
      visibilitySaving = false;
    }
  }

  async function startSelecting() {
    if (settingsDirty) {
      const proceed = await confirmDialog({
        title: 'Discard unsaved settings?',
        body: 'You have unsaved settings changes. Starting now will discard them.',
        confirmLabel: 'Discard & start',
        danger: true,
      });
      if (!proceed) {
        activeTab = 'settings';
        return;
      }
    }
    starting = true;
    startError = null;
    try {
      // Random mode skips picking entirely — the server fills the queue
      // with chart picks when the room enters the playing phase.
      await api.changePhase(roomData.id, isRandom ? 'playing' : 'selecting');
    } catch (e) {
      startError = e instanceof APIError ? e.message : String(e);
    } finally {
      starting = false;
    }
  }

  async function promote(userId: string, username: string) {
    const ok = await confirmDialog({
      title: `Make ${username} the leader?`,
      body: 'They will take over control of the room.',
      confirmLabel: 'Promote',
    });
    if (!ok) return;
    playerError = null;
    try {
      await api.promotePlayer(roomData.id, userId);
    } catch (e) {
      playerError = e instanceof APIError ? e.message : String(e);
    }
  }

  async function kick(userId: string, username: string) {
    const ok = await confirmDialog({
      title: `Kick ${username}?`,
      body: 'They will be removed from the room.',
      confirmLabel: 'Kick',
      danger: true,
    });
    if (!ok) return;
    playerError = null;
    try {
      await api.kickPlayer(roomData.id, userId);
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
        class="flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors {activeTab ===
        tab.id
          ? 'border-accent text-accent'
          : 'border-transparent text-text-secondary hover:text-text-primary'}"
        onclick={() => (activeTab = tab.id)}
      >
        <span>{tab.label}</span>
        {#if tab.id === 'players'}
          <span class="text-xs text-text-muted">({roomData.players.length})</span>
        {/if}
        {#if tab.id === 'settings' && settingsDirty}
          <span
            class="h-2 w-2 rounded-full bg-orange-500"
            title="unsaved changes"
            aria-label="unsaved changes"
          ></span>
        {/if}
      </button>
    {/each}
  </nav>

  <div class:hidden={activeTab !== 'players'}>
    <div class="grid gap-6 md:grid-cols-[2fr_1fr]">
      <div class="card">
        <h2 class="mb-3 text-lg font-semibold">
          Players <span class="text-text-muted">({roomData.players.length})</span>
        </h2>
        <PlayerList
          players={roomData.players}
          leaderId={roomData.leader_id}
          highlightUserId={auth.user?.id ?? null}
          roomId={roomData.id}
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
          <p class="text-sm text-text-muted">Room name</p>
          {#if isLeader}
            <form
              class="mt-1 flex items-center gap-2"
              onsubmit={(e) => { e.preventDefault(); saveName(); }}
            >
              <input
                id="room-name-input"
                class="input flex-1"
                placeholder="Untitled room"
                maxlength="64"
                bind:value={nameDraft}
                disabled={nameSaving}
                onblur={saveName}
              />
              {#if (roomData.name ?? '') !== nameDraft.trim()}
                <button
                  type="submit"
                  class="btn-secondary text-xs"
                  disabled={nameSaving}
                >
                  {nameSaving ? 'Saving…' : 'Save'}
                </button>
              {/if}
            </form>
            {#if nameError}
              <p class="mt-1 text-xs text-danger">{nameError}</p>
            {/if}
          {:else}
            <p class="mt-1 text-base text-text-primary">
              {roomData.name ?? 'Untitled room'}
            </p>
          {/if}
        </div>

        <div>
          <label class="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={roomData.is_public}
              disabled={!isLeader || visibilitySaving}
              onchange={toggleVisibility}
            />
            <span>
              Public
              <span class="text-text-muted">— show in the public browser</span>
            </span>
          </label>
          {#if visibilityError}
            <p class="mt-1 text-xs text-danger">{visibilityError}</p>
          {/if}
        </div>

        <hr class="border-border" />

        {#if roomData.code}
          <div>
            <p class="text-sm text-text-muted">Join code</p>
            <div class="mt-1 flex items-center gap-2">
              <span class="font-mono text-2xl font-bold tracking-widest text-accent">
                {roomData.code}
              </span>
              <button
                type="button"
                class="btn-ghost text-xs"
                onclick={() => copy(roomData.code!, 'Code')}
              >
                Copy
              </button>
            </div>
          </div>
        {:else}
          <div>
            <p class="text-sm text-text-muted">Join code</p>
            <p class="mt-1 text-sm text-text-secondary">
              None — this room is publicly listed. Anyone signed in can join
              from the home page browser.
            </p>
          </div>
        {/if}

        <div>
          <p class="text-sm text-text-muted">
            {roomData.is_public ? 'Direct link' : 'Invite link (one-click join)'}
          </p>
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
          {#if !roomData.is_public}
            <p class="mt-2 text-xs text-text-muted">
              Friends opening this link will be added to the room automatically.
            </p>
          {/if}
        </div>
        {#if copyHint}
          <p class="text-xs text-success">{copyHint}</p>
        {/if}
      </div>
    </div>
  </div>

  <div class:hidden={activeTab !== 'settings'}>
    <SettingsPanel roomData={roomData} readonly={!isLeader} bind:dirty={settingsDirty} />
  </div>

  <div class:hidden={activeTab !== 'start'}>
    <div class="card space-y-4">
      <h2 class="text-lg font-semibold">Start the game</h2>
      {#if isLeader}
        <p class="text-sm text-text-secondary">
          {#if isRandom}
            Random mode: as soon as you start, the server will draw {roomData
              .settings.random_song_count} popular tracks and the game begins —
            no song selection phase.
          {:else}
            Once you start, players will pick their songs in the next phase.
            Make sure your settings look right under the Settings tab.
          {/if}
        </p>
        {#if settingsDirty}
          <div class="rounded-md border border-orange-500/40 bg-orange-500/10 p-3 text-sm text-orange-500">
            <p class="font-medium">You have unsaved settings changes.</p>
            <p class="mt-1 text-xs">
              Save them in the Settings tab first, or starting will discard them.
            </p>
          </div>
        {/if}
        {#if startError}
          <p class="text-sm text-danger">{startError}</p>
        {/if}
        {@const minPlayers = isRandom ? 1 : 2}
        {@const tooFew = roomData.players.length < minPlayers}
        <button
          class="btn-primary w-full"
          disabled={starting || tooFew}
          onclick={startSelecting}
          title={tooFew
            ? 'need at least 2 players — solo games have no one to guess'
            : ''}
        >
          {starting
            ? 'Starting…'
            : tooFew
              ? 'Need at least 2 players'
              : isRandom
                ? 'Start the game'
                : 'Start song selection'}
        </button>
      {:else}
        <p class="text-sm text-text-secondary">
          Waiting for the leader to start the round. They control when the game
          begins.
        </p>
      {/if}
    </div>
  </div>
</div>
