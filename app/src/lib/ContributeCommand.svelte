<script lang="ts">
	import { onDestroy } from 'svelte';

	const COPY_CMD = 'uvx cc-sentiment';

	let copied = $state(false);
	let copyTimeout: ReturnType<typeof setTimeout> | null = null;

	async function copy() {
		await navigator.clipboard.writeText(COPY_CMD);
		copied = true;
		if (copyTimeout) clearTimeout(copyTimeout);
		copyTimeout = setTimeout(() => {
			copied = false;
		}, 1500);
	}

	onDestroy(() => {
		if (copyTimeout) clearTimeout(copyTimeout);
	});
</script>

<div class="flex items-center gap-2 rounded border border-border bg-bg-code px-3 py-2">
	<code class="flex-1 truncate font-mono text-xs text-text-secondary">$ {COPY_CMD}</code>
	<button
		type="button"
		onclick={copy}
		aria-label={copied ? 'Copied' : 'Copy command'}
		class="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium text-accent transition-colors hover:bg-bg-hover hover:text-accent-hover"
	>
		{copied ? 'Copied' : 'Copy'}
	</button>
</div>
