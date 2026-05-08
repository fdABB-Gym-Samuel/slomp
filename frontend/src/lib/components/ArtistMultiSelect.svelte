<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';
  import { api, APIError } from '$lib/api';
  import type { ArtistSummary } from '$lib/types';

  let {
    selected = $bindable(),
    readonly = false,
  }: {
    selected: string[];
    readonly?: boolean;
  } = $props();

  let query = $state('');
  let results = $state<ArtistSummary[]>([]);
  let searching = $state(false);
  let error = $state<string | null>(null);

  // Cache id → ArtistSummary so we can render chips for selected ids.
  let cache = $state<Record<string, ArtistSummary>>({});
  // Track ids we've already requested so we don't refetch on every effect run.
  let requested = new SvelteSet<string>();

  let timer: ReturnType<typeof setTimeout> | null = null;

  // When `selected` contains ids we've never seen (e.g. settings loaded
  // from the server after a reload or game restart), fetch their details
  // so the chips render with names/images instead of truncated ids.
  $effect(() => {
    const missing = selected.filter(
      (id) => !cache[id] && !requested.has(id),
    );
    if (missing.length === 0) return;
    for (const id of missing) requested.add(id);
    api
      .spotifyGetArtists(missing)
      .then((artists) => {
        for (const a of artists) cache[a.id] = a;
      })
      .catch(() => {
        for (const id of missing) requested.delete(id);
      });
  });

  function onInput() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(runSearch, 300);
  }

  async function runSearch() {
    const q = query.trim();
    if (q.length < 1) {
      results = [];
      return;
    }
    searching = true;
    error = null;
    try {
      results = await api.spotifySearchArtists(q);
      for (const a of results) cache[a.id] = a;
    } catch (e) {
      error = e instanceof APIError ? e.message : String(e);
    } finally {
      searching = false;
    }
  }

  function add(a: ArtistSummary) {
    if (!selected.includes(a.id)) {
      cache[a.id] = a;
      selected = [...selected, a.id];
    }
    query = '';
    results = [];
  }

  function remove(id: string) {
    selected = selected.filter((x) => x !== id);
  }
</script>

<div>
  <p class="label mb-1">Required artists (track must be by one of these)</p>

  {#if selected.length > 0}
    <div class="mb-2 flex flex-wrap gap-2">
      {#each selected as id (id)}
        {@const a = cache[id]}
        {#if readonly}
          <span class="badge bg-accent/20 text-accent inline-flex items-center gap-1">
            {#if a?.image_url}
              <img src={a.image_url} alt="" class="h-4 w-4 rounded-full" />
            {/if}
            {a?.name ?? id.slice(0, 8)}
          </span>
        {:else}
          <button
            type="button"
            class="badge bg-accent/20 text-accent hover:bg-accent/30 inline-flex items-center gap-1"
            onclick={() => remove(id)}
          >
            {#if a?.image_url}
              <img src={a.image_url} alt="" class="h-4 w-4 rounded-full" />
            {/if}
            {a?.name ?? id.slice(0, 8)}
            <span class="ml-1 text-xs">×</span>
          </button>
        {/if}
      {/each}
    </div>
  {:else if readonly}
    <p class="text-sm text-text-muted">Any artist allowed.</p>
  {/if}

  {#if !readonly}
  <input
    class="input text-sm"
    placeholder="search Spotify for an artist…"
    bind:value={query}
    oninput={onInput}
  />

  {#if searching}
    <p class="mt-2 text-xs text-text-muted">searching…</p>
  {/if}

  {#if error}
    <p class="mt-2 text-xs text-danger">{error}</p>
  {/if}

  {#if results.length > 0}
    <ul
      class="mt-2 max-h-48 overflow-y-auto rounded-md border border-border bg-surface-raised"
    >
      {#each results as a (a.id)}
        <li>
          <button
            type="button"
            class="flex w-full items-center gap-3 p-2 text-left hover:bg-surface"
            disabled={selected.includes(a.id)}
            onclick={() => add(a)}
          >
            {#if a.image_url}
              <img
                src={a.image_url}
                alt=""
                class="h-8 w-8 flex-shrink-0 rounded-full"
              />
            {:else}
              <div class="h-8 w-8 flex-shrink-0 rounded-full bg-surface"></div>
            {/if}
            <span class="min-w-0 flex-1 truncate text-sm">{a.name}</span>
            {#if selected.includes(a.id)}
              <span class="text-xs text-success">added</span>
            {:else}
              <span class="text-xs text-text-muted">+</span>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
  {/if}
</div>
