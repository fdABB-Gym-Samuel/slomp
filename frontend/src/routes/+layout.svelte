<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { auth } from '$lib/auth.svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';

  let { children } = $props();

  const PUBLIC_ROUTES = ['/login', '/register'];

  onMount(async () => {
    await auth.refresh();
    const path = $page.url.pathname;
    if (!auth.user && !PUBLIC_ROUTES.includes(path)) {
      goto('/login', { replaceState: true });
    }
  });
</script>

{#if auth.loading}
  <div class="flex min-h-screen items-center justify-center">
    <p class="text-text-muted">Loading…</p>
  </div>
{:else}
  {@render children()}
{/if}
