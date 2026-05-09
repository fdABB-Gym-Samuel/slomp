<script lang="ts">
  import { confirmStore } from '$lib/confirm.svelte';

  function onKeydown(e: KeyboardEvent) {
    if (!confirmStore.current) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      confirmStore.answer(false);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      confirmStore.answer(true);
    }
  }
</script>

<svelte:window on:keydown={onKeydown} />

{#if confirmStore.current}
  {@const c = confirmStore.current}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center p-4"
    role="dialog"
    aria-modal="true"
    aria-labelledby="confirm-title"
    tabindex="-1"
  >
    <!-- Click-to-dismiss backdrop. Escape on window:keydown handles the
         keyboard equivalent, so the visual layer is fine to be a button. -->
    <button
      type="button"
      class="absolute inset-0 cursor-default bg-black/60"
      aria-label="Cancel"
      onclick={() => confirmStore.answer(false)}
    ></button>
    <div class="card relative w-full max-w-sm">
      <h2 id="confirm-title" class="mb-2 text-lg font-semibold">{c.title}</h2>
      {#if c.body}
        <p class="mb-4 text-sm text-text-secondary">{c.body}</p>
      {/if}
      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          class="btn-ghost"
          onclick={() => confirmStore.answer(false)}
        >
          {c.cancelLabel}
        </button>
        <!-- svelte-ignore a11y_autofocus -->
        <button
          type="button"
          class={c.danger ? 'btn-danger' : 'btn-primary'}
          autofocus
          onclick={() => confirmStore.answer(true)}
        >
          {c.confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}
