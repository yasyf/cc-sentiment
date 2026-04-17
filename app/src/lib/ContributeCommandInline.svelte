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

<p class="mt-1 text-sm text-text-dim">
	Add your data now:
	<button
		type="button"
		onclick={copy}
		title={copied ? 'Copied!' : 'Click to copy'}
		aria-label={copied ? 'Copied' : `Copy command: ${COPY_CMD}`}
		class="cursor-pointer select-none rounded font-mono text-text-secondary transition-colors hover:text-text focus-visible:outline focus-visible:outline-1 focus-visible:outline-border"
	>$ {COPY_CMD}</button>
	{#if copied}<span class="ml-1">copied</span>{/if}
</p>
