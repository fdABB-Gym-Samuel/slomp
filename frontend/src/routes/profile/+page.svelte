<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';

  let usernameInput = $state('');
  let usernamePassword = $state('');
  let usernameSubmitting = $state(false);
  let usernameError = $state<string | null>(null);
  let usernameSuccess = $state(false);

  let currentPassword = $state('');
  let newPassword = $state('');
  let confirmPassword = $state('');
  let passwordSubmitting = $state(false);
  let passwordError = $state<string | null>(null);
  let passwordSuccess = $state(false);

  onMount(() => {
    if (!auth.user) {
      goto('/login');
      return;
    }
    usernameInput = auth.user.username;
  });

  async function submitUsername(e: Event) {
    e.preventDefault();
    if (!auth.user) return;
    usernameSubmitting = true;
    usernameError = null;
    usernameSuccess = false;
    try {
      await auth.changeUsername(usernameInput, usernamePassword);
      usernameSuccess = true;
      usernamePassword = '';
    } catch (err) {
      usernameError = err instanceof APIError ? err.message : String(err);
    } finally {
      usernameSubmitting = false;
    }
  }

  async function submitPassword(e: Event) {
    e.preventDefault();
    passwordError = null;
    passwordSuccess = false;
    if (newPassword !== confirmPassword) {
      passwordError = 'New passwords do not match';
      return;
    }
    passwordSubmitting = true;
    try {
      await auth.changePassword(currentPassword, newPassword);
      passwordSuccess = true;
      currentPassword = '';
      newPassword = '';
      confirmPassword = '';
    } catch (err) {
      passwordError = err instanceof APIError ? err.message : String(err);
    } finally {
      passwordSubmitting = false;
    }
  }
</script>

<div class="mx-auto max-w-2xl space-y-8 p-6 md:p-10">
  <header class="space-y-1">
    <p class="text-sm text-text-muted">Profile</p>
    <h1 class="text-2xl font-bold text-text-primary">Account settings</h1>
  </header>

  <section class="card space-y-4">
    <div>
      <h2 class="text-lg font-semibold text-text-primary">Username</h2>
      <p class="text-sm text-text-secondary">Change your display name. Requires your current password.</p>
    </div>
    <form class="space-y-4" onsubmit={submitUsername}>
      <div>
        <label class="label" for="username">Username</label>
        <input
          id="username"
          class="input mt-1"
          autocomplete="username"
          bind:value={usernameInput}
          required
          minlength="3"
          maxlength="32"
        />
      </div>
      <div>
        <label class="label" for="username-password">Current password</label>
        <input
          id="username-password"
          type="password"
          class="input mt-1"
          autocomplete="current-password"
          bind:value={usernamePassword}
          required
        />
      </div>
      {#if usernameError}
        <p class="text-sm text-danger">{usernameError}</p>
      {/if}
      {#if usernameSuccess}
        <p class="text-sm text-success">Username updated.</p>
      {/if}
      <button
        class="btn-primary"
        disabled={usernameSubmitting ||
          !usernameInput.trim() ||
          !usernamePassword ||
          usernameInput === auth.user?.username}
      >
        {usernameSubmitting ? 'Saving…' : 'Save username'}
      </button>
    </form>
  </section>

  <section class="card space-y-4">
    <div>
      <h2 class="text-lg font-semibold text-text-primary">Password</h2>
      <p class="text-sm text-text-secondary">Choose a new password (at least 8 characters).</p>
    </div>
    <form class="space-y-4" onsubmit={submitPassword}>
      <div>
        <label class="label" for="current-password">Current password</label>
        <input
          id="current-password"
          type="password"
          class="input mt-1"
          autocomplete="current-password"
          bind:value={currentPassword}
          required
        />
      </div>
      <div>
        <label class="label" for="new-password">New password</label>
        <input
          id="new-password"
          type="password"
          class="input mt-1"
          autocomplete="new-password"
          bind:value={newPassword}
          required
          minlength="8"
        />
      </div>
      <div>
        <label class="label" for="confirm-password">Confirm new password</label>
        <input
          id="confirm-password"
          type="password"
          class="input mt-1"
          autocomplete="new-password"
          bind:value={confirmPassword}
          required
          minlength="8"
        />
      </div>
      {#if passwordError}
        <p class="text-sm text-danger">{passwordError}</p>
      {/if}
      {#if passwordSuccess}
        <p class="text-sm text-success">Password updated.</p>
      {/if}
      <button
        class="btn-primary"
        disabled={passwordSubmitting || !currentPassword || !newPassword || !confirmPassword}
      >
        {passwordSubmitting ? 'Saving…' : 'Save password'}
      </button>
    </form>
  </section>

  <a href="/" class="inline-block text-sm text-text-secondary hover:text-text-primary">← Back</a>
</div>
