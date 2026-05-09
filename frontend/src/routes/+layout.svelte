<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { auth } from '$lib/auth.svelte';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

  let { children } = $props();

  onMount(async () => {
    await auth.refresh();
  });
</script>

{#if auth.loading}
  <div class="flex min-h-screen items-center justify-center">
    <p class="text-text-muted">Loading…</p>
  </div>
{:else}
  {@render children()}
{/if}

<ConfirmDialog />
