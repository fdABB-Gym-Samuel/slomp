<script lang="ts">
  import { goto } from '$app/navigation';
  import { APIError } from '$lib/api';
  import { auth } from '$lib/auth.svelte';

  let username = $state('');
  let password = $state('');
  let submitting = $state(false);
  let error = $state<string | null>(null);

  async function submit(e: Event) {
    e.preventDefault();
    submitting = true;
    error = null;
    try {
      await auth.register(username, password);
      goto('/');
    } catch (err) {
      error = err instanceof APIError ? err.message : String(err);
    } finally {
      submitting = false;
    }
  }
</script>

<div class="mx-auto flex min-h-screen max-w-md items-center p-8">
  <div class="w-full">
    <h1 class="mb-2 text-3xl font-bold text-accent">slomp</h1>
    <p class="mb-8 text-text-secondary">Pick a name to play under.</p>

    <form class="card space-y-4" onsubmit={submit}>
      <div>
        <label class="label" for="username">Username</label>
        <input
          id="username"
          class="input mt-1"
          autocomplete="username"
          bind:value={username}
          required
          minlength="3"
          maxlength="32"
        />
        <p class="mt-1 text-xs text-text-muted">3–32 chars, case-insensitive</p>
      </div>
      <div>
        <label class="label" for="password">Password</label>
        <input
          id="password"
          type="password"
          class="input mt-1"
          autocomplete="new-password"
          bind:value={password}
          required
          minlength="8"
        />
        <p class="mt-1 text-xs text-text-muted">at least 8 chars</p>
      </div>
      {#if error}
        <p class="text-sm text-danger">{error}</p>
      {/if}
      <button class="btn-primary w-full" disabled={submitting}>
        {submitting ? 'Creating…' : 'Create account'}
      </button>
    </form>

    <p class="mt-6 text-center text-sm text-text-secondary">
      Already have one?
      <a href="/login" class="text-accent hover:text-accent-hover">Log in</a>
    </p>
  </div>
</div>
