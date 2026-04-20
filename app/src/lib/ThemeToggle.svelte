<script lang="ts">
	import { userPrefersMode, setMode } from 'mode-watcher';

	const NEXT: Record<string, 'light' | 'dark' | 'system'> = {
		system: 'light',
		light: 'dark',
		dark: 'system'
	};

	const current = $derived(userPrefersMode.current ?? 'system');
</script>

<button
	type="button"
	class="text-text-muted hover:text-text transition-colors text-base leading-none"
	aria-label={`Theme: ${current}. Click to change.`}
	title={`Theme: ${current}`}
	onclick={() => setMode(NEXT[current])}
>
	{#if current === 'dark'}🌙{:else if current === 'light'}☀️{:else}🖥️{/if}
</button>
