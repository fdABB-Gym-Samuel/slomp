<script lang="ts">
  import { goto } from '$app/navigation';
  import { auth } from '$lib/auth.svelte';

  async function logout() {
    await auth.logout();
    goto('/login');
  }
</script>

{#if auth.user}
  <nav
    class="flex items-center justify-end gap-3 border-b border-border bg-surface/40 px-6 py-2 text-sm md:px-10"
  >
    <a
      href="/profile"
      class="text-text-secondary transition-colors hover:text-text-primary"
    >
      {auth.user.username}
    </a>
    <button class="btn-ghost text-sm" onclick={logout}>Log out</button>
  </nav>
{/if}
